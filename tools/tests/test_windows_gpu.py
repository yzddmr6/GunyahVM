# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Windows GPU acceleration: host requirements, profile choice, VMM argv and media."""

import base64
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from gunyahvm import config, core, crosvmargs, windows_gpu, windows_vm
from gunyahvm.errors import Failure
from test_capabilities import capable
from test_core_boundaries import vm_config

ROOT = Path(__file__).resolve().parents[2]
MACOS_VMM = "/usr/lib/gunyahvm/macos/crosvm/crosvm"


def gpu_host():
    snap = capable()
    snap.udmabuf = True
    snap.vhost_net = True
    snap.runtime["components"]["crosvm"]["features"].append("vhost-net")
    snap.runtime["components"].update({
        "uefi": {"version": "t", "features": ["windows-ecam"], "files": {}},
        "windows_drivers": {"version": "t", "features": [], "files": {}},
        "windows_openssh": {"version": "t", "features": ["offline"], "files": {}},
        "windows_gpu": {"version": "20.15.1.43+turnip.e265de74be32", "features": ["wddm"],
                        "files": {"inventory": "/usr/share/gunyahvm/windows/gpu/files.json",
                                  "certificate": "/usr/share/gunyahvm/windows/gpu/DroidVM-GPU-Test.cer"}},
        "macos": {"version": "t", "features": ["vm-fd-share", "windows-wddm"],
                  "files": {"binary": MACOS_VMM,
                            "renderer": "/usr/lib/gunyahvm/macos/renderer/libvirglrenderer.so.1"}},
    })
    return snap


def gpu_config(install_media=False):
    cfg = vm_config(windows=True)
    cfg["display"]["gpu"] = "native"
    cfg["memory_mode"] = "shared"
    cfg["install_media"] = install_media
    cfg["vhost_net"] = True
    return config.validate(cfg)


@pytest.fixture
def vm(tmp_path):
    return tmp_path


def test_complete_host_runs_the_gpu_profile_and_each_gap_is_named():
    assert windows_gpu.requirements(gpu_host()) == []
    assert gpu_host().modes(guest_os="windows11") == ["simplefb", "native"]
    gaps = [
        (lambda s: s.runtime["components"].pop("windows_gpu"), "GPU driver"),
        (lambda s: s.runtime["components"]["macos"]["features"].remove("windows-wddm"), "VMM"),
        (lambda s: setattr(s, "protected_share", False), "share guest memory"),
        (lambda s: setattr(s, "render_nodes", []), "Adreno"),
        (lambda s: setattr(s, "udmabuf", False), "udmabuf"),
    ]
    for remove, reason in gaps:
        snap = gpu_host()
        remove(snap)
        assert any(reason in r for r in windows_gpu.requirements(snap))
        # The basic display stays available whatever the GPU lacks.
        assert snap.modes(guest_os="windows11") == ["simplefb"]


def test_sharing_unknown_before_the_drivers_load_is_not_reported_missing():
    snap = gpu_host()
    snap.protected_share = None
    snap.drivers_loadable = True
    assert windows_gpu.requirements(snap) == []
    snap.drivers_loadable = False
    assert windows_gpu.requirements(snap)


def test_gpu_profile_shares_memory_and_basic_display_keeps_it_protected():
    cfg = vm_config(windows=True)
    for gpu, memory in (("native", "protected"), ("simplefb", "shared"), ("virgl", "protected")):
        bad = json.loads(json.dumps(cfg))
        bad["display"]["gpu"], bad["memory_mode"] = gpu, memory
        with pytest.raises(ValueError):
            config.validate(bad)
    assert gpu_config()["memory_mode"] == "shared"
    linux = vm_config()
    linux.update(memory_mode="shared")
    linux["display"]["gpu"] = "native"
    with pytest.raises(ValueError, match="native graphics"):
        config.validate(linux)


def test_installed_windows_boots_the_droidvm_vmm_with_the_gpu(vm):
    argv, env = crosvmargs.build(vm, gpu_config(), vm / "runs/one", gpu_host(), "/run/user/1000/gvm/display")
    assert argv[0] == MACOS_VMM
    option = lambda name: argv[argv.index(name) + 1]
    assert "--protected-vm-pseudo-unprotected" in argv and "--swiotlb" not in argv
    assert option("--pre-alloc") == f"drm-host-mb={windows_gpu.POOL_MIB}"
    gpu = option("--gpu")
    assert "context-types=virgl:virgl2:drm" in gpu and "udmabuf=true" in gpu
    assert f"vram-mb={windows_gpu.VRAM_MIB}" in gpu and "mode=windowed[1280,720]" in gpu
    assert option("--wayland-sock") == "/run/user/1000/gvm/display"
    # One display: a firmware framebuffer would give Windows a second adapter.
    assert "--simplefb" not in argv
    # 1.4.0 Windows I/O: io_uring disk workers and vhost-net.
    assert option("--block").endswith(",async-executor=uring,multiple-workers=true")
    assert option("--net").endswith(",vhost-net")
    assert argv[-1] == str(vm / "firmware.fd")
    assert env["GPU_DISPLAY_COPY_MODE"] == "cpu"
    # A VMM without the window pointer keeps the single-touch mapping.
    assert "CROSVM_GVM_INPUT_MODE" not in env
    host = gpu_host()
    host.runtime["components"]["macos"]["features"].append("window-pointer")
    _, env = crosvmargs.build(vm, gpu_config(), vm / "runs/one", host, "/run/user/1000/gvm/display")
    assert env["CROSVM_GVM_INPUT_MODE"] == "tablet"
    assert env["LD_LIBRARY_PATH"].split(":") == [
        "/usr/lib/gunyahvm/macos/crosvm", "/usr/lib/gunyahvm/macos/renderer"]


def test_installation_media_keeps_the_protected_basic_display(vm):
    runtime = gpu_host()
    runtime.runtime["components"]["crosvm"]["features"].append("vnc")
    argv, _ = crosvmargs.build(vm, gpu_config(install_media=True), vm / "runs/one", runtime,
                               "/run/user/1000/gvm/display")
    assert argv[0] == "/usr/lib/gunyahvm/crosvm/crosvm"
    assert "--protected-vm-without-firmware" in argv and argv[argv.index("--swiotlb") + 1] == "256"
    assert "--simplefb" in argv and "--gpu" not in argv


def test_gpu_profile_refuses_a_host_without_memory_sharing(vm):
    snap = gpu_host()
    snap.protected_share = None
    snap.drivers_loadable = True  # starting loaded the drivers, so unknown is a refusal now
    with pytest.raises(ValueError, match="share guest memory"):
        crosvmargs.build(vm, gpu_config(), vm / "runs/one", snap, "/run/user/1000/gvm/display")


@pytest.mark.parametrize("snapshot,request_gpu,vnc,expected", [
    (gpu_host, None, False, "native"),
    (capable, None, False, "simplefb"),
    (gpu_host, None, True, "simplefb"),
    (gpu_host, "simplefb", False, "simplefb"),
])
def test_new_windows_uses_the_gpu_when_the_host_can(monkeypatch, snapshot, request_gpu, vnc, expected):
    snap = snapshot()
    snap.runtime["components"].setdefault("uefi", {"version": "t", "features": ["windows-ecam"], "files": {}})
    snap.runtime["components"].setdefault("windows_drivers", {"version": "t", "features": [], "files": {}})
    snap.runtime["components"].setdefault("windows_openssh", {"version": "t", "features": ["offline"], "files": {}})
    monkeypatch.setattr(core.capabilities, "probe", lambda: snap)
    args = SimpleNamespace(gpu=request_gpu, vnc=vnc, vnc_port=5900, vhost_net=None)
    cfg = core.new_config(vm_config(windows=True), args)
    assert cfg["display"]["gpu"] == expected
    assert cfg["memory_mode"] == ("shared" if expected == "native" else "protected")


def test_explicit_gpu_request_on_an_incapable_host_fails(monkeypatch):
    snap = gpu_host()
    snap.udmabuf = False
    monkeypatch.setattr(core.capabilities, "probe", lambda: snap)
    args = SimpleNamespace(gpu="native", vnc=False, vnc_port=5900, vhost_net=None)
    with pytest.raises(Failure, match="udmabuf"):
        core.new_config(vm_config(windows=True), args)


def test_start_prepares_host_memory_for_every_shared_memory_boot(monkeypatch, tmp_path):
    calls = []
    import gunyahvm.privilege as privilege
    monkeypatch.setattr(privilege, "start", lambda *a, **k: calls.append(k["guest"]))
    for cfg, guest in ((gpu_config(), "shared"), (gpu_config(install_media=True), "other"),
                       (vm_config(windows=True), "other")):
        monkeypatch.setattr(core, "owned_vm", lambda *a, c=cfg, **k: (tmp_path, c))
        core.start_vm(tmp_path, "test")
        assert calls.pop() == guest


def load_prepare_windows():
    path = ROOT / "src/gunyahvm/windows/prepare-windows.py"
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("prepare_windows", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def gpu_payload(tmp_path):
    directory = tmp_path / "gpu"
    (directory / "driver").mkdir(parents=True)
    inventory = {}
    for name, data in (("driver/virtio_wddm_rs.inf", b"inf"), ("driver/vulkan_freedreno.dll", b"dll"),
                       ("DroidVM-GPU-Test.cer", b"cer")):
        (directory / name).write_bytes(data)
        import hashlib
        inventory["gpu/" + name] = {"sha256": hashlib.sha256(data).hexdigest()}
    (directory / "files.json").write_text(json.dumps(inventory))
    return directory


def test_media_preparation_checks_the_gpu_driver_before_using_it(tmp_path):
    module = load_prepare_windows()
    directory = gpu_payload(tmp_path)
    module.verify_gpu_driver(directory)
    (directory / "driver/extra.dll").write_bytes(b"unpinned")
    with pytest.raises(ValueError, match="differ"):
        module.verify_gpu_driver(directory)
    (directory / "driver/extra.dll").unlink()
    (directory / "driver/vulkan_freedreno.dll").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        module.verify_gpu_driver(directory)


def test_new_media_stages_the_gpu_driver_and_records_it(monkeypatch, tmp_path):
    runtime = {"components": {"uefi": {"files": {"image": str(tmp_path / "firmware.fd")}},
                              "windows_drivers": {"files": {"inventory": str(tmp_path / "assets/driver-files.json")}},
                              "windows_gpu": {"version": "gpu-test",
                                              "files": {"inventory": str(tmp_path / "gpu/files.json")}}}}
    (tmp_path / "firmware.fd").write_bytes(b"fw")
    script = tmp_path / "bundle/windows/prepare-windows.py"
    script.parent.mkdir(parents=True)
    script.write_text("")
    (tmp_path / "iso").write_bytes(b"iso")
    commands = []

    def run(command, **kwargs):
        if command[0] == "ssh-keygen":
            (tmp_path / "vm/agent-key.pub").write_text("ssh-ed25519 AAAA test")
            return subprocess.CompletedProcess(command, 0)
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps({"edition": {"name": "Pro"}}))

    monkeypatch.setattr(windows_vm.components, "load", lambda: runtime)
    monkeypatch.setattr(windows_vm.subprocess, "run", run)
    (tmp_path / "vm").mkdir()
    cfg = vm_config(windows=True)
    args = SimpleNamespace(iso=tmp_path / "iso", edition=None, local_user=None, password_fd=None)
    windows_vm.prepare(tmp_path / "vm", cfg, args, tmp_path / "bundle")
    command = commands[0]
    assert command[command.index("--gpu-driver") + 1] == str(tmp_path / "gpu")
    assert cfg["windows"]["gpu_driver"] == "gpu-test"
    config.validate(cfg)


def test_first_boot_trusts_the_gpu_driver_and_checks_the_pool_only_where_it_exists(tmp_path):
    from gunyahvm.windows import windows_deploy

    windows_deploy.write_payload(tmp_path, 3, {"username": "u", "password": "p"}, "1" * 36, agent=True)
    first = (tmp_path / "first-boot.ps1").read_text()
    assert "DroidVM-GPU-Test.cer" in first and "TrustedPublisher" in first
    guard = first.index("ACPI\\RDMA0000")
    assert guard < first.index("--rdma") < first.index("--power --installed")
    # The control account is still set up on a shared-memory (GPU) first boot.
    assert first.index("--power --installed") < first.index("agent-setup.ps1")


def test_existing_windows_gets_the_driver_over_its_control_channel(monkeypatch, tmp_path):
    vm = tmp_path / "vm"
    (vm / "gpu").mkdir(parents=True)
    runtime = gpu_host().runtime
    runtime["components"]["windows_gpu"]["files"]["inventory"] = str(vm / "gpu/files.json")
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        out = "GUNYAHVM_GPU_DRIVER_STAGED\n" if "-EncodedCommand" in command[-1] else ""
        return subprocess.CompletedProcess(command, 0, stdout=out, stderr="")

    monkeypatch.setattr(windows_gpu.subprocess, "run", run)
    cfg = vm_config(windows=True)
    version = windows_gpu.install_driver(vm, cfg, "10.203.5.9", runtime)
    assert version == runtime["components"]["windows_gpu"]["version"]
    scp = next(c for c in commands if c[0] == "scp")
    assert scp[-1] == "gunyahvm-agent@10.203.5.9:C:/gunyahvm/wingpu/"
    assert str(vm / "gpu/driver") in scp and str(vm / "gpu/DroidVM-GPU-Test.cer") in scp
    assert "-i" in scp and str(vm / "agent-key") in scp
    encoded = commands[-1][-1].rsplit(" ", 1)[1]
    script = base64.b64decode(encoded).decode("utf-16le")
    assert script == windows_gpu.INSTALL_SCRIPT and "pnputil.exe /add-driver" in script


def test_driver_install_switches_the_next_start_to_the_gpu(monkeypatch, tmp_path):
    cfg = vm_config(windows=True)
    vm = tmp_path / "vms/test"
    vm.mkdir(parents=True)
    (vm / "config.json").write_text(json.dumps(cfg))
    monkeypatch.setattr(core.capabilities, "probe", gpu_host)
    monkeypatch.setattr(core, "owned_vm", lambda *a, **k: (vm, json.loads((vm / "config.json").read_text())))
    monkeypatch.setattr(core, "status", lambda *a: {"state": "running", "guest_ready": True, "ipv4": "10.203.5.9"})
    monkeypatch.setattr(windows_gpu, "install_driver", lambda *a: "gpu-test")
    assert core.install_windows_gpu_driver(tmp_path, "test") == "gpu-test"
    saved = json.loads((vm / "config.json").read_text())
    assert saved["display"]["gpu"] == "native" and saved["memory_mode"] == "shared"
    assert saved["windows"]["gpu_driver"] == "gpu-test"
    monkeypatch.setattr(core, "status", lambda *a: {"state": "running", "guest_ready": False})
    with pytest.raises(Failure, match="ready"):
        core.install_windows_gpu_driver(tmp_path, "test")


def test_gpu_display_is_not_claimed_reconnectable(monkeypatch, tmp_path):
    socket_path = tmp_path / "display"
    import socket
    server = socket.socket(socket.AF_UNIX)
    server.bind(str(socket_path))
    monkeypatch.setattr(core, "status", lambda *a: {"state": "running", "vmm_component": "macos",
                                                    "display_socket": str(socket_path),
                                                    "run_dir": str(tmp_path)})
    monkeypatch.setattr(core, "vmm_active", lambda state: True)
    runtime = gpu_host().runtime
    runtime["components"]["macos"]["features"].append("wayland-reconnect")
    monkeypatch.setattr(core.components, "load", lambda: runtime)
    with pytest.raises(Failure, match="Restart this virtual machine"):
        core.reconnect_display(tmp_path, gpu_config(), str(socket_path))
    server.close()


def test_vmm_patches_apply_in_lock_order_and_are_pinned():
    import hashlib
    lock = json.loads((ROOT / "tools/macos.lock.json").read_text())
    patches = lock["host_sources"]["patches"]["crosvm"]
    assert [Path(p["file"]).name for p in patches] == [
        "crosvm-macos.patch", "crosvm-pr12-reset-resource-component.patch",
        "crosvm-windows-window-input.patch"]
    for patch in patches:
        assert hashlib.sha256((ROOT / patch["file"]).read_bytes()).hexdigest() == patch["sha256"]
    # The rebuilt VMM is the pinned one; only crosvm changed from the 1.3.0 input.
    assert lock["host_sources"]["artifacts"]["usr/lib/gunyahvm/macos/crosvm/crosvm"] == \
        lock["input"]["files"]["usr/lib/gunyahvm/macos/crosvm/crosvm"]["sha256"]
    assert {"windows-wddm", "window-pointer"} <= set(lock["component"]["features"])
