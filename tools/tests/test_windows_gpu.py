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


def gpu_config(install_media=False, setup_done=True):
    cfg = vm_config(windows=True)
    cfg["display"]["gpu"] = "native"
    cfg["memory_mode"] = "shared"
    cfg["install_media"] = install_media
    cfg["vhost_net"] = True
    cfg["windows"]["gpu_driver"] = "20.15.1.43+turnip.e265de74be32"
    cfg["windows"]["setup_done"] = setup_done
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


def test_gpu_profile_shares_memory_and_basic_display_may_share_it():
    cfg = vm_config(windows=True)
    for gpu, memory in (("native", "protected"), ("virgl", "protected")):
        bad = json.loads(json.dumps(cfg))
        bad["display"]["gpu"], bad["memory_mode"] = gpu, memory
        with pytest.raises(ValueError):
            config.validate(bad)
    assert gpu_config()["memory_mode"] == "shared"
    # The way back from the GPU: the basic display with shared memory.
    back = json.loads(json.dumps(cfg))
    back["memory_mode"] = "shared"
    config.validate(back)
    for fact in ("setup_done", "shared_boot"):
        bad = json.loads(json.dumps(cfg))
        bad["windows"][fact] = "yes"
        with pytest.raises(ValueError, match=fact):
            config.validate(bad)
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
    assert "--no-rng" in argv
    assert option("--pre-alloc") == f"drm-host-mb={windows_gpu.POOL_MIB}"
    gpu = option("--gpu")
    assert "context-types=virgl:virgl2:drm" in gpu and "udmabuf=true" in gpu
    assert f"vram-mb={windows_gpu.VRAM_MIB}" in gpu and "mode=windowed[1280,720]" in gpu
    assert ",pci-address=00:08.0," in gpu
    assert option("--wayland-sock") == "/run/user/1000/gvm/display"
    # One display: a firmware framebuffer would give Windows a second adapter.
    assert "--simplefb" not in argv
    # 1.4.0 Windows I/O: io_uring disk workers and vhost-net, in the disk's and card's slots.
    assert option("--block").endswith(",async-executor=uring,multiple-workers=true,pci-address=00:01.0")
    assert option("--net").endswith(",vhost-net,pci-address=00:02.0")
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
    # WinPE's partitioning script takes the first disk as the target and the next two as
    # the boot and installation media.
    blocks = [argv[i + 1] for i, a in enumerate(argv) if a == "--block"]
    assert [b.rsplit(",pci-address=", 1)[1] for b in blocks] == [
        "00:01.0", "00:10.0", "00:11.0", "00:12.0"]
    assert [Path(b.split(",")[0]).name for b in blocks] == [
        "disk.raw", "boot.raw", "install.raw", "drivers.raw"]


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
    # The deployment's first boot turns Modern Standby off.
    assert cfg["windows"]["standby_off"] is True
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
    assert script == windows_gpu.INSTALL_SCRIPT.replace("__CONTROL_USER__", "gunyahvm-agent")
    assert "pnputil.exe /add-driver" in script


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
        "crosvm-windows-window-input.patch", "crosvm-device-thread-name.patch",
        "crosvm-half-float-scanout.patch", "crosvm-wayland-scanout-blit.patch"]
    for patch in patches:
        assert hashlib.sha256((ROOT / patch["file"]).read_bytes()).hexdigest() == patch["sha256"]
    # The rebuilt VMM is the pinned one; only crosvm changed from the 1.3.0 input.
    assert lock["host_sources"]["artifacts"]["usr/lib/gunyahvm/macos/crosvm/crosvm"] == \
        lock["input"]["files"]["usr/lib/gunyahvm/macos/crosvm/crosvm"]["sha256"]
    assert {"windows-wddm", "window-pointer"} <= set(lock["component"]["features"])


def test_windows_setup_runs_on_the_protected_basic_display(vm):
    """Until the first sign-in has finished, a VM set to the GPU starts on the basic display."""
    for cfg in (gpu_config(setup_done=False), gpu_config(install_media=False, setup_done=True)):
        if cfg["windows"]["setup_done"]:
            cfg["windows"]["gpu_driver"] = ""  # nothing staged: no GPU start either
        argv, env = crosvmargs.build(vm, cfg, vm / "runs/one", gpu_host(), "/run/user/1000/gvm/display")
        assert argv[0] == "/usr/lib/gunyahvm/crosvm/crosvm"
        assert "--protected-vm-without-firmware" in argv and "--simplefb" in argv
        assert "--gpu" not in argv
    argv, _ = crosvmargs.build(vm, gpu_config(), vm / "runs/one", gpu_host(), "/run/user/1000/gvm/display")
    assert argv[0] == MACOS_VMM and "--gpu" in argv and "--simplefb" not in argv


def test_basic_display_after_a_shared_start_keeps_shared_memory(vm):
    """The way back from the GPU: same VMM and memory, no virtio-gpu, firmware framebuffer."""
    host = gpu_host()
    host.runtime["components"]["macos"]["features"].append("window-pointer")
    cfg = gpu_config()
    cfg["windows"]["shared_boot"] = True
    cfg["display"]["gpu"] = "simplefb"
    cfg["memory_mode"] = windows_gpu.memory_mode(cfg, "simplefb")
    assert cfg["memory_mode"] == "shared"
    argv, env = crosvmargs.build(vm, config.validate(cfg), vm / "runs/one", host, "/run/user/1000/gvm/display")
    option = lambda name: argv[argv.index(name) + 1]
    assert argv[0] == MACOS_VMM
    assert "--protected-vm-pseudo-unprotected" in argv and "--swiotlb" not in argv
    assert "--gpu" not in argv and "--pre-alloc" not in argv
    assert option("--simplefb").endswith(",wayland-socket=/run/user/1000/gvm/display")
    assert option("--simplefb").startswith("width=1280,height=720,")
    assert "--display-window-keyboard" in argv and "--display-window-mouse" in argv
    assert "GPU_DISPLAY_COPY_MODE" not in env and env["CROSVM_GVM_INPUT_MODE"] == "tablet"
    # The same disk and network as the GPU start, so Windows sees the same devices.
    gpu_argv, _ = crosvmargs.build(vm, gpu_config(), vm / "runs/one", host, "/run/user/1000/gvm/display")
    assert option("--block") == gpu_argv[gpu_argv.index("--block") + 1]
    # A GPU-configured VM that cannot start with the GPU also keeps shared memory.
    unready = gpu_config(setup_done=False)
    unready["windows"]["shared_boot"] = True
    assert windows_gpu.shared_basic(windows_gpu.boot_profile(unready))
    # Before any shared start the basic display stays protected.
    fresh = vm_config(windows=True)
    assert windows_gpu.memory_mode(fresh, "simplefb") == "protected"
    assert windows_gpu.memory_mode(fresh, "native") == "shared"
    lacking = gpu_host()
    lacking.runtime["components"]["macos"]["features"].remove("vm-fd-share")
    with pytest.raises(ValueError, match="basic display with shared memory"):
        crosvmargs.build(vm, config.validate(cfg), vm / "runs/one", lacking, "/run/user/1000/gvm/display")


def test_windows_setup_counts_as_finished_only_on_the_desktop(tmp_path):
    from gunyahvm.windows import windows_deploy

    for account in ({"username": "u", "password": "p"}, None):
        out = tmp_path / ("a" if account else "b")
        out.mkdir()
        windows_deploy.write_payload(out, 3, account, "1" * 36, agent=True)
        script = (out / "first-logon.ps1").read_text()
        # The first sign-in script runs before the per-user setup pages (privacy
        # settings), which do not draw with the GPU driver: it must not write the
        # marker itself, only leave it to Explorer's RunOnce when the desktop starts.
        assert windows_gpu.SETUP_MARKER == "C:\\gunyahvm\\setup-complete"
        assert "New-Item -ItemType File" not in script
        runonce = "$runonce = 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\RunOnce'"
        assert runonce in script
        entry = ("Set-ItemProperty -LiteralPath $runonce -Name GunyahVMSetupComplete "
                 "-Value 'cmd.exe /d /c type nul > " + windows_gpu.SETUP_MARKER + "'")
        assert script.rstrip().endswith(entry)
        assert script.count(windows_gpu.SETUP_MARKER) == 1
        if not account:
            assert script.index("agent-setup.ps1") < script.index(runonce)


def test_setup_turns_off_modern_standby(tmp_path):
    from gunyahvm.windows import windows_deploy

    windows_deploy.write_payload(tmp_path, 3, {"username": "u", "password": "p"}, "1" * 36, agent=True)
    first = (tmp_path / "first-boot.ps1").read_text()
    off = first.index("foreach ($standby in @('standby-timeout-ac', 'standby-timeout-dc'))")
    assert "powercfg.exe /change $standby 0" in first[off:]
    assert "powercfg.exe /hibernate off" in first
    assert off < first.index("'GUNYAHVM_SPECIALIZE_COMPLETE'")


def test_supervisor_turns_off_standby_once_on_older_windows(monkeypatch, tmp_path):
    from gunyahvm import windows_agent

    vm = tmp_path / "vm"
    vm.mkdir()
    cfg = gpu_config()
    cfg["windows"].pop("standby_off", None)
    cfg["windows"]["setup_pages_off"] = True
    (vm / "config.json").write_text(json.dumps(cfg))
    sent, answer = [], {"ok": False}

    def command(vm_dir, cfg_, ip, cmd):
        sent.append(cmd)
        out = "GUNYAHVM_STANDBY_OFF\r\n" if answer["ok"] else ""
        return subprocess.CompletedProcess(cmd, 0 if answer["ok"] else 1, stdout=out, stderr="")

    monkeypatch.setattr(windows_agent, "command", command)
    # Refused: nothing recorded, tried again on the next check.
    cfg = core.windows_ready(vm, cfg, "10.203.5.9", {})
    assert sent == [windows_agent.STANDBY_OFF] and not cfg["windows"].get("standby_off")
    answer["ok"] = True
    cfg = core.windows_ready(vm, cfg, "10.203.5.9", {})
    assert sent == [windows_agent.STANDBY_OFF] * 2 and cfg["windows"]["standby_off"] is True
    assert json.loads((vm / "config.json").read_text())["windows"]["standby_off"] is True
    # Done once: later starts leave Windows alone.
    core.windows_ready(vm, config.validate(json.loads((vm / "config.json").read_text())), "10.203.5.9", {})
    assert len(sent) == 2
    for cmd in ("powercfg /change standby-timeout-ac 0", "powercfg /change standby-timeout-dc 0"):
        assert cmd in windows_agent.STANDBY_OFF


def test_supervisor_still_records_setup_done_when_the_desktop_marker_appears(monkeypatch, tmp_path):
    from gunyahvm import windows_agent

    vm = tmp_path / "vm"
    vm.mkdir()
    cfg = gpu_config(setup_done=False)
    cfg["windows"].update(standby_off=True, setup_pages_off=True)
    (vm / "config.json").write_text(json.dumps(cfg))
    sent = []

    def command(vm_dir, cfg_, ip, cmd):
        sent.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="GUNYAHVM_SETUP_COMPLETE\r\n", stderr="")

    monkeypatch.setattr(windows_agent, "command", command)
    state = {"display_profile": "simplefb"}
    cfg = core.windows_ready(vm, cfg, "10.203.5.9", state)
    assert cfg["windows"]["setup_done"] and state["gpu_next_start"]
    assert sent == [windows_gpu.SETUP_CHECK]


def test_setup_check_reads_the_marker_over_the_control_channel(monkeypatch, vm):
    seen = []

    def command(vm_dir, cfg, ip, cmd):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout=reply, stderr="")

    from gunyahvm import windows_agent

    monkeypatch.setattr(windows_agent, "command", command)
    cfg = gpu_config(setup_done=False)
    reply = "GUNYAHVM_SETUP_COMPLETE\r\n"
    assert windows_gpu.setup_complete(vm, cfg, "10.203.5.9")
    reply = ""
    assert not windows_gpu.setup_complete(vm, cfg, "10.203.5.9")
    assert seen[0] == windows_gpu.SETUP_CHECK
    # Setup counts as finished only once its pages have closed too (GitHub issue #1).
    assert seen[0].startswith("if exist C:\\gunyahvm\\setup-complete (")
    assert 'tasklist /fi "imagename eq wwahost.exe"' in seen[0]
    assert seen[0].endswith("|| echo GUNYAHVM_SETUP_COMPLETE)")


def test_supervisor_records_windows_facts_without_losing_settings(tmp_path):
    vm = tmp_path / "vm"
    vm.mkdir()
    cfg = gpu_config(setup_done=False)
    stored = json.loads(json.dumps(cfg))
    stored["cpus"] = 6  # changed in settings after this start
    (vm / "config.json").write_text(json.dumps(stored))
    updated = core.record_windows(vm, cfg, setup_done=True, shared_boot=True)
    assert updated["windows"]["setup_done"] and updated["windows"]["shared_boot"]
    on_disk = json.loads((vm / "config.json").read_text())
    assert on_disk["windows"]["setup_done"] and on_disk["windows"]["shared_boot"]
    assert on_disk["cpus"] == 6 and cfg["windows"]["setup_done"] is False
    # Another operation holds the VM: nothing is written and the caller retries.
    from gunyahvm import storage

    with storage.operation_lock(vm):
        again = core.record_windows(vm, cfg, gpu_driver="other")
    assert again is cfg
    assert json.loads((vm / "config.json").read_text())["windows"]["gpu_driver"] == cfg["windows"]["gpu_driver"]


def test_existing_windows_needs_a_signed_in_user_before_its_driver(monkeypatch, tmp_path):
    vm = tmp_path / "vm"
    (vm / "gpu").mkdir(parents=True)
    runtime = gpu_host().runtime
    runtime["components"]["windows_gpu"]["files"]["inventory"] = str(vm / "gpu/files.json")
    scripts = []

    def run(command, **kwargs):
        if "-EncodedCommand" in command[-1]:
            scripts.append(base64.b64decode(command[-1].split()[-1]).decode("utf-16le"))
            return subprocess.CompletedProcess(command, 0, stdout="GUNYAHVM_GPU_DRIVER_STAGED\n", stderr="")
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(windows_gpu.subprocess, "run", run)
    cfg = vm_config(windows=True)
    cfg["windows"]["ssh_user"] = "control"
    windows_gpu.install_driver(vm, cfg, "10.203.5.9", runtime)
    script = scripts[0]
    assert "$control = 'control'" in script and "__CONTROL_USER__" not in script
    assert "-notin @($control, 'defaultuser0')" in script
    assert "Finish Windows setup and sign in once" in script
    assert script.index("pnputil.exe") < script.index("New-Item -ItemType File -Force -Path $marker")


def test_existing_windows_signs_the_patched_driver_catalog_before_pnputil():
    # The shipped package carries a replaced Vulkan driver its own catalog does not list;
    # pnputil only takes it with a catalog that does, signed by a publisher Windows trusts.
    script = windows_gpu.INSTALL_SCRIPT
    trust = script.index("$trust.Add(")
    catalog = script.index("New-FileCatalog -Path $driver")
    sign = script.index("Set-AuthenticodeSignature -FilePath $fresh -Certificate $signer")
    move = script.index("Move-Item -LiteralPath $fresh -Destination $catalog")
    forget = script.index("-DeleteKey")
    install = script.index("pnputil.exe /add-driver (Join-Path $driver 'virtio_wddm_rs.inf')")
    assert trust < catalog < sign < move < forget < install
    assert script.index("$signer = New-SelfSignedCertificate") < script.rindex(
        "foreach ($store in @('Root', 'TrustedPublisher'))", 0, trust)
    assert "if ($signed.Status -ne 'Valid') { throw" in script
    # The private key goes whatever happens, so the certificate signs only this catalog.
    assert script.index("} finally {") < forget


def policy_hunks(patch):
    """(file, removed prctl rule, added prctl rule) for each policy file a patch changes."""
    out, current, minus = [], None, None
    for line in Path(patch).read_text().splitlines():
        if line.startswith("+++ b/"):
            current = line[6:]
        elif line.startswith("-prctl:"):
            minus = line[1:]
        elif line.startswith("+prctl:"):
            out.append((current, minus, line[1:]))
            minus = None
        elif line[:1] in ("+", "-") and not line.startswith(("+++", "---")):
            raise AssertionError("the patch changes more than prctl rules: " + line)
    return out


@pytest.mark.parametrize("patch,count", [
    ("third_party/crosvm/patches/0043-jail-let-aarch64-device-processes-read-their-thread-name.patch", 25),
    ("third_party/runtime/macos/crosvm-device-thread-name.patch", 25),
])
def test_device_jails_may_read_their_thread_name(patch, count):
    """A SIGBUS reporter reads the thread name; seccomp must not turn the fault into SIGSYS."""
    hunks = policy_hunks(ROOT / patch)
    assert len(hunks) == count
    for path, before, after in hunks:
        assert path.startswith("jail/seccomp/aarch64/") and path.endswith(".policy")
        assert "PR_GET_NAME" not in before and before.startswith("prctl: arg0 == PR_SET_NAME")
        assert after == before.replace("PR_SET_NAME", "PR_SET_NAME || arg0 == PR_GET_NAME", 1)
    digest = __import__("hashlib").sha256((ROOT / patch).read_bytes()).hexdigest()
    locks = (ROOT / "third_party/runtime/sources.json").read_text() + (ROOT / "tools/macos.lock.json").read_text()
    assert patch in locks and digest in locks


def pci_addresses(argv):
    """{device option: [PCI address, ...]} for the devices placed in a fixed slot."""
    found = {}
    for option, value in zip(argv, argv[1:]):
        if option in ("--block", "--net", "--gpu") and ",pci-address=" in value:
            found.setdefault(option, []).append(value.split(",pci-address=")[1].split(",")[0])
    return found


def test_every_windows_profile_shows_the_same_devices_in_the_same_slots(vm):
    """The disk and the network card keep their slots, so Windows keeps them and their settings."""
    host = gpu_host()
    host.runtime["components"]["macos"]["features"].append("window-pointer")
    display = "/run/user/1000/gvm/display"
    shared = gpu_config()
    shared["windows"]["shared_boot"] = True
    shared["display"]["gpu"] = "simplefb"
    profiles = {name: crosvmargs.build(vm, config.validate(cfg), vm / "runs/one", host, display)[0]
                for name, cfg in (("basic", vm_config(windows=True)), ("gpu", gpu_config()),
                                  ("shared", shared))}
    assert profiles["basic"][0] != profiles["gpu"][0] == profiles["shared"][0] == MACOS_VMM
    for name, argv in profiles.items():
        slots = pci_addresses(argv)
        assert slots["--block"] == ["00:01.0"] and slots["--net"] == ["00:02.0"], name
        assert slots.get("--gpu") == (["00:08.0"] if name == "gpu" else None), name
    # Input devices take the free slots from 00:03.0 in both VMMs: nothing fixed lies there.
    assert len(set(windows_vm.PCI.values())) == len(windows_vm.PCI)
    assert not {"00:03.0", "00:04.0", "00:05.0", "00:06.0", "00:07.0"} & set(windows_vm.PCI.values())


def vmm_log(*lines):
    """VMM stderr lines as crosvm writes them, from (UTC time, message) pairs."""
    return "".join(f"[2026-11-23T{t}.000000000+00:00 INFO  devices::virtio::gpu] {m}\n"
                   for t, m in lines)


RESET = "gpu reset: scanout Some(0) restored 1280x800 -> 1280x800, surface dropped"


@pytest.mark.parametrize("lines, loop", [
    # GitHub issue #1: the driver's start, then five TDRs in 15 s and the guest's reset.
    ([("14:16:08", RESET), ("14:21:08", RESET), ("14:21:14", RESET), ("14:21:16", RESET),
      ("14:21:19", RESET), ("14:21:23", RESET), ("14:21:30", "vcpu requested reset")], True),
    # An ordinary restart: only the driver's start reset the device.
    ([("14:16:08", RESET), ("16:40:00", "vcpu requested reset")], False),
    # Two TDRs Windows recovered from, hours apart.
    ([("14:16:08", RESET), ("15:00:00", RESET), ("17:00:00", RESET),
      ("17:30:00", "vcpu requested reset")], False),
    # A TDR loop Windows survived long before an ordinary restart.
    ([("14:16:08", RESET), ("14:21:08", RESET), ("14:21:14", RESET), ("14:21:16", RESET),
      ("18:00:00", "vcpu requested reset")], False),
])
def test_tdr_loop_is_several_gpu_resets_just_before_the_guest_reset(tmp_path, lines, loop):
    log = tmp_path / "crosvm.stderr"
    log.write_text("GH: unstamped line\n" + vmm_log(*lines))
    assert windows_gpu.tdr_loop(log) is loop


def test_tdr_loop_reads_only_the_end_of_a_long_log(tmp_path, monkeypatch):
    log = tmp_path / "crosvm.stderr"
    noise = vmm_log(*[("14:20:00", "GPU RESOURCE_ATTACH_BACKING complete")] * 2000)
    log.write_text(vmm_log(("14:16:08", RESET)) + noise + vmm_log(
        ("14:21:08", RESET), ("14:21:14", RESET), ("14:21:30", "vcpu requested reset")))
    assert not windows_gpu.tdr_loop(log)
    monkeypatch.setattr(windows_gpu, "TDR_TAIL", len(noise) // 2)
    assert not windows_gpu.tdr_loop(log)
    log.write_text(log.read_text() + vmm_log(("14:21:31", RESET)))
    assert windows_gpu.tdr_loop(log)
    assert not windows_gpu.tdr_loop(tmp_path / "missing")


def test_basic_display_keeps_shared_memory_after_a_shared_start():
    cfg = gpu_config()
    cfg["windows"]["shared_boot"] = True
    basic = windows_gpu.basic_display(cfg)
    assert basic["display"]["gpu"] == "simplefb" and basic["memory_mode"] == "shared"
    assert cfg["display"]["gpu"] == "native"
    config.validate(basic)


def test_supervisor_restarts_a_tdr_loop_on_the_basic_display(monkeypatch, tmp_path):
    from gunyahvm import storage, windows_agent

    vm = tmp_path / "vm"
    vm.mkdir()
    cfg = gpu_config()
    cfg["windows"].update(shared_boot=True, standby_off=True)
    storage.atomic_json(vm / "config.json", cfg)
    storage.atomic_json(vm / "state.json", {"state": "created"})
    monkeypatch.setattr(core, "access_limits", lambda c: (6 * 1024**3, 6 * 1024**3))
    monkeypatch.setattr(core, "host_snapshot", gpu_host)
    monkeypatch.setattr(core.capabilities, "sensors", lambda p: {"batteries": [], "temperatures": []})
    monkeypatch.setattr(core.signal, "signal", lambda *a: None)
    monkeypatch.setattr(core, "network_action", lambda *a: None)
    monkeypatch.setattr(windows_agent, "guest_ip", lambda c: None)
    monkeypatch.setattr(windows_agent, "neighbour_events", lambda tap: None)

    class Guard:
        def __init__(self, stop):
            pass

        def close(self):
            pass

    monkeypatch.setattr(core, "SleepGuard", Guard)
    boots = []
    hung = vmm_log(("14:16:08", RESET), ("14:21:08", RESET), ("14:21:14", RESET),
                   ("14:21:16", RESET), ("14:21:30", "vcpu requested reset"))

    def build(vm_, cfg_, rd, snapshot, wayland):
        profile = windows_gpu.boot_profile(cfg_)
        boots.append((profile["display"]["gpu"], profile["memory_mode"]))
        if len(boots) == 1:  # the GPU hangs: Windows gives up and resets the VM
            return [sys.executable, "-c", f"import sys; sys.stderr.write({hung!r}); sys.exit(32)"], {}
        return [sys.executable, "-c", "pass"], {}

    monkeypatch.setattr(core.crosvmargs, "build", build)
    assert core.supervise(vm, cfg) == 0
    assert boots == [("native", "shared"), ("simplefb", "shared")]
    stored = config.validate(json.loads((vm / "config.json").read_text()))
    assert stored["display"]["gpu"] == "simplefb" and stored["memory_mode"] == "shared"
    state = json.loads((vm / "state.json").read_text())
    assert state["state"] == "stopped" and state["display_profile"] == "simplefb"
    assert state["gpu_hang"]["log"].endswith("crosvm.stderr")
    # The next start the owner makes no longer reports the hang.
    first = sorted((vm / "runs").iterdir())[0]
    assert "gpu reset" in (first / "crosvm.stderr").read_text()


def test_an_ordinary_guest_restart_keeps_the_gpu(monkeypatch, tmp_path):
    log = tmp_path / "crosvm.stderr"
    log.write_text(vmm_log(("14:16:08", RESET), ("16:40:00", "vcpu requested reset")))
    assert not windows_gpu.tdr_loop(log)


def test_supervisor_turns_the_setup_pages_off_once(monkeypatch, tmp_path):
    from gunyahvm import windows_agent

    vm = tmp_path / "vm"
    vm.mkdir()
    cfg = gpu_config()
    cfg["windows"]["standby_off"] = True
    (vm / "config.json").write_text(json.dumps(cfg))
    sent, answer = [], {"ok": False}

    def command(vm_dir, cfg_, ip, cmd, timeout=15):
        sent.append(cmd)
        assert timeout > 15  # PowerShell's first start in a fresh Windows is slow
        out = "GUNYAHVM_SETUP_PAGES_OFF\r\n" if answer["ok"] else ""
        return subprocess.CompletedProcess(cmd, 0 if answer["ok"] else 1, stdout=out, stderr="")

    monkeypatch.setattr(windows_agent, "command", command)
    run = {}
    cfg = core.windows_ready(vm, cfg, "10.203.5.9", run)
    assert len(sent) == 1 and not cfg["windows"].get("setup_pages_off")
    # Refused: not again in this start (readiness is checked every few seconds) ...
    core.windows_ready(vm, cfg, "10.203.5.9", run)
    assert len(sent) == 1
    # ... but again in the next one.
    answer["ok"] = True
    cfg = core.windows_ready(vm, cfg, "10.203.5.9", {})
    assert cfg["windows"]["setup_pages_off"] is True
    assert json.loads((vm / "config.json").read_text())["windows"]["setup_pages_off"] is True
    core.windows_ready(vm, config.validate(json.loads((vm / "config.json").read_text())), "10.203.5.9", {})
    assert len(sent) == 2
    prefix = "powershell -NoProfile -NonInteractive -EncodedCommand "
    script = base64.b64decode(sent[0][len(prefix):]).decode("utf-16le")
    assert sent[0].startswith(prefix) and script == windows_agent.SETUP_PAGES_OFF
    # Every account and the default profile, through reg.exe so no hive stays open.
    for needle in ("ScoobeSystemSettingEnabled", "SubscribedContent-310093Enabled",
                   r"Users\Default\NTUSER.DAT", "reg.exe load", "reg.exe unload"):
        assert needle in script


def test_driver_install_waits_for_open_setup_pages():
    script = windows_gpu.INSTALL_SCRIPT
    assert script.index("Get-Process -Name wwahost") < script.index("pnputil.exe /add-driver")


def test_slow_setup_pages_step_does_not_hold_up_setup_done(monkeypatch, tmp_path):
    from gunyahvm import windows_agent

    vm = tmp_path / "vm"
    vm.mkdir()
    cfg = gpu_config(setup_done=False)
    cfg["windows"]["standby_off"] = True
    (vm / "config.json").write_text(json.dumps(cfg))

    def command(vm_dir, cfg_, ip, cmd, timeout=15):
        if cmd.startswith("powershell"):
            raise subprocess.TimeoutExpired(cmd, timeout)
        return subprocess.CompletedProcess(cmd, 0, stdout="GUNYAHVM_SETUP_COMPLETE\r\n", stderr="")

    monkeypatch.setattr(windows_agent, "command", command)
    cfg = core.windows_ready(vm, cfg, "10.203.5.9", {"display_profile": "simplefb"})
    assert cfg["windows"]["setup_done"] and not cfg["windows"].get("setup_pages_off")
