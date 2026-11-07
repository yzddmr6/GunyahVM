# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# SafeOpen, memory, VNC, input, Windows shutdown and state collection cases adapted
# from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7 tests.
from copy import deepcopy
import os
import subprocess
import pytest
from gunyahvm import config, crosvmargs, storage, lifecycle, windows_agent, privileged
from gunyahvm.monitor import StateMonitor
from test_capabilities import capable


def vm_config(windows=False):
    return config.complete_new(
        {
            "id": "11111111-1111-4111-8111-111111111111",
            "uid": os.getuid(),
            "gid": os.getgid(),
            "name": "test",
            "os": "windows11" if windows else "linux",
            "cpus": 2,
            "memory_mib": 4608 if windows else 1024,
            "disk_gib": 64 if windows else 8,
            "subnet": "10.203.5.0/24",
            "vhost_net": False,
            "username": "ubuntu",
            "install_media": False,
        }
    )


@pytest.fixture
def vm(tmp_path):
    (tmp_path / "boot").mkdir()
    (tmp_path / "boot/cmdline").write_text("root=LABEL=cloudimg-rootfs ro")
    return tmp_path


def build(vm, cfg, snap=None):
    return crosvmargs.build(
        vm, cfg, vm / "runs/one", snap or capable(), "/run/user/1000/wayland-0"
    )


def test_safe_open_refuses_symlink_owner_and_fifo(tmp_path):
    real = tmp_path / "disk.raw"
    real.touch()
    link = tmp_path / "link"
    link.symlink_to(real)
    os.close(storage.open_nofollow(real, os.O_RDONLY, os.getuid()))
    for path, owner in [(link, os.getuid()), (real, os.getuid() + 1)]:
        with pytest.raises((OSError, RuntimeError)):
            storage.open_nofollow(path, os.O_RDONLY, owner)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    with pytest.raises(RuntimeError):
        storage.open_nofollow(fifo, os.O_RDONLY)


def test_protected_and_shared_args(vm):
    cfg = vm_config()
    args, _ = build(vm, cfg)
    assert "--protected-vm-without-firmware" in args and "--disable-sandbox" not in args
    assert args[args.index("--swiotlb") + 1] == "128"
    cfg.update(memory_mode="shared", guest_payload=True)
    args, _ = build(vm, cfg)
    assert "--protected-vm-pseudo-unprotected" in args and "--swiotlb" not in args
    assert "--disable-sandbox" in args
    cfg["display"]["gpu"] = "native"
    with pytest.raises(ValueError, match="shared"):
        build(vm, cfg)


@pytest.mark.parametrize("mode", ["virgl", "venus", "native"])
def test_capsets_and_guestaccept(vm, mode):
    cfg = vm_config()
    cfg["display"]["gpu"] = mode
    cfg["guest_payload"] = True
    args, env = build(vm, cfg)
    gpu = args[args.index("--gpu") + 1]
    assert {
        "virgl": "virgl:virgl2,",
        "venus": "virgl:virgl2:venus,",
        "native": "virgl:virgl2:drm,",
    }[mode] in gpu
    assert env["CROSVM_GVM_ACCEPT"] == ("0" if mode == "virgl" else "1")
    assert ("--gpu-render-server" in args) == (mode == "venus")
    assert ("--disable-sandbox" in args) == (mode != "virgl")
    snap = capable()
    snap.protected_share = False
    if mode == "virgl":
        args, env = build(vm, cfg, snap)
        assert env["CROSVM_GVM_ACCEPT"] == "0" and "--disable-sandbox" not in args
        snap.protected_share = True
        snap.runtime["components"]["guest_kernel"]["features"].remove("guest-accept")
        args, env = build(vm, cfg, snap)
        assert env["CROSVM_GVM_ACCEPT"] == "0" and "--disable-sandbox" not in args
    else:
        with pytest.raises(ValueError, match="display"):
            build(vm, cfg, snap)


@pytest.mark.parametrize("mode", config.INPUT_MODES)
def test_window_input_and_loopback_vnc(vm, mode):
    cfg = vm_config()
    cfg["display"].update(gpu="simplefb", vnc=True, input_mode=mode, fullscreen=True)
    args, env = build(vm, cfg)
    assert args[args.index("--vnc-server") + 1] == "host=127.0.0.1,port=5900"
    assert "--wayland-sock" not in args
    assert env["CROSVM_GVM_INPUT_MODE"] == mode
    assert env["CROSVM_GVM_FULLSCREEN"] == "1"


def test_balloon_switch_only_when_compiled(vm):
    cfg, snap = vm_config(), capable()
    assert "--no-balloon" not in build(vm, cfg, snap)[0]
    snap.runtime["components"]["crosvm"]["features"].append("balloon")
    assert "--no-balloon" in build(vm, cfg, snap)[0]


def test_schema_and_boolean_resource_constraints():
    cfg = vm_config()
    config.validate(cfg)
    for key, value in [
        ("schema", 1),
        ("cpus", True),
        ("memory_mode", "open"),
        ("backend", "qemu"),
        ("vhost_net", "yes"),
    ]:
        bad = deepcopy(cfg)
        bad[key] = value
        with pytest.raises(ValueError):
            config.validate(bad)
    bad = deepcopy(cfg)
    bad["display"]["input_mode"] = "typo"
    with pytest.raises(ValueError):
        config.validate(bad)


def test_network_identity_is_uid_and_uuid_bound():
    ident = vm_config()["id"]
    a = config.network_identity(ident, 1000)
    b = config.network_identity(ident, 1001)
    assert all(a[k] != b[k] for k in a)
    assert len(a["tap"]) <= 15
    with pytest.raises(ValueError, match="RFC1918"):
        config.private_subnet("127.0.0.0/24")


def test_shared_paths_cannot_inject_backend_arguments(vm):
    cfg = vm_config()
    cfg["shares"] = [{"path": "/home/user/a:tag:type=fs", "tag": "share", "ro": False}]
    with pytest.raises(ValueError, match="separators"):
        build(vm, cfg)


def test_windows_ssh_identity_and_failure_are_explicit(monkeypatch, vm):
    args = windows_agent.win_ssh_argv(
        ["/a", "/b"], "gvm", "10.203.5.2", "/kh", "shutdown.exe /s /t 0"
    )
    assert [args[i + 1] for i, a in enumerate(args) if a == "-i"] == ["/a", "/b"]
    for option in (
        "BatchMode=yes",
        "PasswordAuthentication=no",
        "IdentitiesOnly=yes",
        "StrictHostKeyChecking=accept-new",
    ):
        assert option in args
    monkeypatch.setattr(
        windows_agent,
        "command",
        lambda *a: subprocess.CompletedProcess([], 255, "", "key refused"),
    )
    with pytest.raises(ValueError, match="key refused"):
        windows_agent.poweroff(vm, vm_config(True), "10.203.5.2")


def test_clean_stop_needs_ack_and_force_never_counts():
    state = lifecycle.transition({"state": "created"}, "starting")
    state = lifecycle.transition(state, "running")
    with pytest.raises(ValueError):
        lifecycle.transition(state, "created")
    for forced, acknowledged, code, expected in [
        (False, True, 0, True),
        (False, False, 0, False),
        (True, True, 0, False),
        (False, True, -9, False),
    ]:
        assert (
            lifecycle.outcome(code, forced, True, acknowledged)["clean_shutdown"]
            is expected
        )


def test_monitor_collects_once_and_keeps_previous_on_failure():
    calls, updates = [], []
    values = [({"vm": {"status": {"state": "running"}}}, 1), None]
    monitor = StateMonitor(
        lambda *v: updates.append(v),
        collect=lambda ids: (calls.append(ids), values.pop(0))[1],
    )
    monitor.set_vms(["vm"])
    assert monitor.poll_once()[1] == 1
    assert monitor.poll_once()[1] == 1
    assert len(calls) == 2 and len(updates) == 1


def test_loader_only_requests_present_vm_functions(tmp_path):
    modules = tmp_path / "lib/modules/test"
    modules.mkdir(parents=True)
    (modules / "modules.dep").write_text(
        "kernel/drivers/virt/gunyah/gunyah_rsc_mgr.ko.zst: dep.ko\nkernel/drivers/virt/gunyah/gunyah_vcpu.ko:\nkernel/drivers/virt/gunyah/gunyah_qcom.ko:\nkernel/drivers/unrelated.ko:\n"
    )
    (tmp_path / "sys/module/gunyah_vcpu").mkdir(parents=True)
    assert privileged.module_names(tmp_path, "test") == ["gunyah_rsc_mgr"]


def test_forward_is_loopback_and_close_disconnects_existing_clients():
    import socket
    from gunyahvm.portproxy import TcpForward

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        server.settimeout(3)
        proxy = TcpForward(0, server.getsockname())
        try:
            assert proxy.ls.getsockname()[0] == "127.0.0.1"
            with socket.create_connection(proxy.ls.getsockname(), timeout=3) as client:
                upstream, _ = server.accept()
                with upstream:
                    upstream.settimeout(3)
                    client.sendall(b"hello")
                    assert upstream.recv(5) == b"hello"
                    upstream.sendall(b"world")
                    assert client.recv(5) == b"world"
                    proxy.close()
                    assert client.recv(1) == b""
        finally:
            proxy.close()


def test_runtime_kernel_does_not_require_the_disks_stock_kernel(tmp_path, monkeypatch):
    from gunyahvm import image

    monkeypatch.setattr(
        image, "image_info", lambda *a: {"format": "raw", "virtual-size": 4096}
    )
    monkeypatch.setattr(image, "gpt_partitions", lambda *a: [(1, 0, 4096)])
    monkeypatch.setattr(image, "run", lambda *a: b"")
    monkeypatch.setattr(
        image,
        "grub_boot",
        lambda data: (
            "/boot/vmlinuz-test",
            "/boot/initrd.img-test",
            "root=LABEL=rootfs ro",
        ),
    )

    def dump(fs, path, output):
        assert path == "/grub/grub.cfg", (
            "runtime boot must not demand the stock kernel/config"
        )
        output.write_text("fixture")
        return True

    monkeypatch.setattr(image, "debug_dump", dump)
    image.extract_boot(tmp_path / "disk.raw", tmp_path, runtime_kernel=True)
    assert (tmp_path / "boot/cmdline").read_text() == "root=LABEL=rootfs ro\n"
    assert not (tmp_path / "boot/Image").exists()


def test_guest_reset_is_a_restart_not_a_clean_shutdown():
    value = lifecycle.outcome(32, False, False, False)
    assert value["state"] == "restarting" and not value["clean_shutdown"]
    assert lifecycle.outcome(32, True, False, False)["state"] == "failed"


def test_mirror_download_uses_only_origin_checksums(tmp_path, monkeypatch):
    import hashlib
    from gunyahvm import image

    payload = b"cloud-image-fixture"
    calls = []

    def download(url, path):
        calls.append(url)
        if path.name == "SHA256SUMS":
            assert url.startswith("https://cloud-images.ubuntu.com/")
            path.write_text(
                hashlib.sha256(payload).hexdigest() + " *" + image.IMAGE_NAME + "\n"
            )
        else:
            assert url.startswith("https://mirrors.tuna.tsinghua.edu.cn/")
            path.write_bytes(payload)

    monkeypatch.setattr(image, "download", download)
    path, origin, digest = image.cloud_image(tmp_path, "20261016", "tuna")
    assert path.read_bytes() == payload and origin.startswith(
        "https://cloud-images.ubuntu.com/"
    )
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        image.cloud_image(tmp_path, "20261016", "tuna")
    assert (
        len(calls) == 3
    )  # origin manifest is authoritative even with a cached mirror image


def test_stopped_windows_config_does_not_probe_ram_or_change_its_firewall_subnet(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    from gunyahvm import core

    cfg = vm_config(True)
    cfg["memory_mib"] = (
        8192  # Stored settings can exceed the current host's launch ceiling.
    )
    store = tmp_path / "store"
    vm = store / "vms" / cfg["name"]
    vm.mkdir(parents=True)
    storage.atomic_json(vm / "config.json", cfg)
    storage.atomic_json(vm / "state.json", {"state": "created"})
    monkeypatch.setattr(
        core.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(tmp_path))
    )

    def forbidden(*a):
        raise AssertionError("configuration loading must not query current host RAM")

    monkeypatch.setattr(core.host, "memory_budget", forbidden)
    assert core.owned_vm(store, cfg["name"], os.getuid(), inputs=False)[1] == cfg
    changed = deepcopy(cfg)
    changed["subnet"] = "10.203.6.0/24"
    with pytest.raises(core.Failure, match="firewall"):
        core.save_config(store, cfg["name"], changed)
