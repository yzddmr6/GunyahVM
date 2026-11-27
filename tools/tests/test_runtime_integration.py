# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""New boundaries: shipped capability manifest and process-local guest Mesa."""

import importlib.util
import json
from pathlib import Path
import pytest
from test_capabilities import capable
from test_core_boundaries import vm_config
from gunyahvm import components, crosvmargs
from runtime_inputs import write_archive

REPO = Path(__file__).resolve().parents[2]


def actual_manifest():
    return json.loads((REPO / "tools/runtime.lock.json").read_text())["manifest"]


@pytest.mark.parametrize("share", [False, True])
def test_shipped_manifest_follows_host_share_and_generates_matching_paths(
    tmp_path, share
):
    snapshot = capable()
    snapshot.runtime = actual_manifest()
    components.validate(snapshot.runtime, check_files=False)
    snapshot.protected_share = share
    assert [m for m in snapshot.modes() if m in ("virgl", "venus", "native")] == (
        ["virgl", "venus", "native"] if share else ["virgl"]
    )
    assert snapshot.features()["balloon_disable"] is False
    vm = tmp_path / "vm"
    (vm / "boot").mkdir(parents=True)
    (vm / "boot/cmdline").write_text("root=UUID=test rw")
    cfg = vm_config()
    cfg["guest_payload"] = True
    cfg["display"].update(gpu="virgl", vnc=True)
    args, env = crosvmargs.build(vm, cfg, tmp_path, snapshot)
    assert env["CROSVM_GVM_ACCEPT"] == "0"
    assert "--disable-sandbox" not in args
    assert "--gpu-render-server" not in args
    assert "--no-balloon" not in args
    if share:
        cfg["display"]["gpu"] = "venus"
        args, env = crosvmargs.build(vm, cfg, tmp_path, snapshot)
        assert env["CROSVM_GVM_ACCEPT"] == "1"
        assert (
            args[args.index("--gpu-render-server") + 1]
            == "path=/usr/lib/gunyahvm/renderer/virgl_render_server"
        )
        assert (
            env["RENDER_SERVER_EXEC_PATH"]
            == "/usr/lib/gunyahvm/renderer/virgl_render_server"
        )
    cfg["display"].update(gpu="none", vnc=False)
    _, env = crosvmargs.build(vm, cfg, tmp_path, snapshot)
    assert env["LD_LIBRARY_PATH"] == "/usr/lib/gunyahvm/crosvm:/usr/lib/gunyahvm/renderer"


def guest_setup():
    spec = importlib.util.spec_from_file_location(
        "guest_setup", REPO / "src/gunyahvm/guest-setup.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_native_guest_setup_does_not_change_global_library_selection(tmp_path):
    root, seed = tmp_path / "root", tmp_path / "seed"
    (root / "etc/gdm3").mkdir(parents=True)
    (root / "etc/os-release").write_text('ID=ubuntu\nVERSION_ID="26.04"\n')
    (root / "etc/ld.so.conf.d").mkdir()
    original = root / "etc/ld.so.conf.d/distribution.conf"
    original.write_text("/usr/lib/aarch64-linux-gnu\n")
    seed.mkdir()
    write_archive(
        seed / "guest-mesa.tar", [("lib/libvulkan_freedreno.so", b"fixture", 0o644)]
    )
    settings = {
        "mode": "native",
        "username": "ubuntu",
        "mac": "02:12:34:56:78:9a",
        "autologin": True,
        "mesa": actual_manifest()["components"]["guest_mesa"],
    }
    (seed / "runtime.json").write_text(json.dumps(settings))
    module = guest_setup()
    module.setup(seed, root)
    assert list((root / "etc/ld.so.conf.d").iterdir()) == [original]
    assert original.read_text() == "/usr/lib/aarch64-linux-gnu\n"
    assert not (root / "etc/vulkan").exists()
    assert "gunyahvm-native.desktop" in (root / "etc/gdm3/custom.conf").read_text()
    assert (root / "usr/share/wayland-sessions/gunyahvm-native.desktop").is_file()
    account = root / "var/lib/AccountsService/users/ubuntu"
    assert "Session=gunyahvm-native\n" in account.read_text()
    account.write_text("[User]\nLanguages=zh_CN.UTF-8\nSession=gunyahvm-native\n")
    settings["mode"] = "virgl"
    (seed / "runtime.json").write_text(json.dumps(settings))
    module.setup(seed, root)
    assert not (root / "usr/share/wayland-sessions/gunyahvm-native.desktop").exists()
    assert "gunyahvm-virgl.desktop" in (root / "etc/gdm3/custom.conf").read_text()
    # GDM's automatic login follows the account's session, so a mode change
    # must replace it there and keep the account's other settings.
    assert account.read_text() == "[User]\nLanguages=zh_CN.UTF-8\nSession=gunyahvm-virgl\n\n"
    assert account.stat().st_mode & 0o777 == 0o600
    settings["mode"] = "none"
    (seed / "runtime.json").write_text(json.dumps(settings))
    module.setup(seed, root)
    assert "Session" not in account.read_text()


def test_native_payload_rejects_other_distribution_abi(tmp_path):
    (tmp_path / "etc").mkdir()
    (tmp_path / "etc/os-release").write_text("ID=debian\nVERSION_ID=13\n")
    (tmp_path / "runtime.json").write_text(
        json.dumps(
            {
                "mode": "native",
                "username": "ubuntu",
                "mac": "02:12:34:56:78:9a",
                "mesa": actual_manifest()["components"]["guest_mesa"],
            }
        )
    )
    with pytest.raises(ValueError, match="ABI mismatch"):
        guest_setup().setup(tmp_path, tmp_path)


def test_guest_graphics_environment_matches_3d_boundary(tmp_path):
    import runpy

    environment = runpy.run_path(str(REPO / "src/gunyahvm/guest-graphics-run"))[
        "environment"
    ]
    assert environment("virgl") == {"VIRGL_DEBUG": "nocoherent"}
    with pytest.raises(ValueError, match="exactly one"):
        environment("venus", icd_directory=tmp_path)
    (tmp_path / "virtio_icd.aarch64.json").write_text("{}")
    venus = environment("venus", icd_directory=tmp_path)
    assert "LD_LIBRARY_PATH" not in venus
    native = environment("native")
    assert native["VK_DRIVER_FILES"].startswith("/usr/lib/gunyahvm/guest-mesa/")
    assert native["LD_LIBRARY_PATH"] == "/usr/lib/gunyahvm/guest-mesa/lib"
    assert native["LIBGL_DRIVERS_PATH"] == native["LD_LIBRARY_PATH"] + "/dri"
    # The desktop session keeps its compositor on virgl and selects only Turnip.
    desktop = environment("native", desktop=True)
    assert desktop["VK_DRIVER_FILES"] == native["VK_DRIVER_FILES"]
    assert not {"LD_LIBRARY_PATH", "LIBGL_DRIVERS_PATH", "GBM_BACKENDS_PATH"} & desktop.keys()


def test_host_gpu_driver_outside_the_standard_directories_reaches_the_sandbox(tmp_path, monkeypatch):
    from gunyahvm import crosvmargs

    (tmp_path / "etc/ld.so.conf.d").mkdir(parents=True)
    (tmp_path / "etc/ld.so.conf").write_text("include /etc/ld.so.conf.d/*.conf\n")
    (tmp_path / "etc/ld.so.conf.d/00-vendor-mesa.conf").write_text("/opt/vendor/mesa/lib\n")
    (tmp_path / "etc/ld.so.conf.d/libc.conf").write_text("# libc\n/usr/local/lib\n")
    (tmp_path / "etc/ld.so.conf.d/aarch64-linux-gnu.conf").write_text("/usr/lib/aarch64-linux-gnu\n")
    for directory in ("opt/vendor/mesa/lib", "usr/local/lib", "usr/lib/aarch64-linux-gnu"):
        (tmp_path / directory).mkdir(parents=True)
    (tmp_path / "opt/vendor/mesa/lib/libEGL.so.1").touch()
    (tmp_path / "usr/lib/aarch64-linux-gnu/libEGL.so.1").touch()
    assert crosvmargs.ld_so_dirs(tmp_path) == [
        "/opt/vendor/mesa/lib", "/usr/lib/aarch64-linux-gnu", "/usr/local/lib"]
    # Only a driver outside the standard directories, and only one that has GL libraries.
    assert crosvmargs.host_gl_dirs(tmp_path) == ["/opt/vendor/mesa/lib"]
    assert crosvmargs.host_gl_dirs(tmp_path / "missing") == []


def test_network_online_follows_the_manager_that_owns_the_network(tmp_path):
    module = guest_setup()
    calls = []

    def run(argv, check=False):
        calls.append(argv[1:])
        return type("Done", (), {"returncode": 0})()

    connections = tmp_path / "run/NetworkManager/system-connections"
    connections.mkdir(parents=True)
    module.align_network_wait(tmp_path, run)
    assert calls == []  # no netplan interface for NetworkManager: leave networkd alone
    (connections / "netplan-vmnet.nmconnection").write_text("[connection]\n")
    networkd = tmp_path / "run/systemd/network"
    networkd.mkdir(parents=True)
    (networkd / "10-netplan-vmnet.network").write_text("[Match]\n")
    module.align_network_wait(tmp_path, run)
    assert calls == []  # systemd-networkd still configures an interface
    (networkd / "10-netplan-vmnet.network").unlink()
    (networkd / "10-netplan-vmnet.link").write_text("[Match]\n")  # a rename only
    module.align_network_wait(tmp_path, run)
    unit = "systemd-networkd-wait-online.service"
    assert calls == [["--quiet", "is-enabled", unit], ["disable", unit], ["stop", unit]]
