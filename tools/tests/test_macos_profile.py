# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Local-input and boot-ABI boundaries; no Apple files or hardware required."""

import hashlib
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from gunyahvm import config, macos


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    source = tmp_path / "bundle"
    source.mkdir()
    manifest = {
        "schema": 2,
        "boot": {"profile": "27.2", "memory_mib": 8192, "cpus": 6,
                 "gpu": True, "agent_port": 2222, "layout": "native-apfs-v2"},
        "files": {},
    }
    for name in macos.INPUTS:
        if name == "agent-key":
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(source / name)], check=True)
            data = (source / name).read_bytes()
        else:
            data = ("fixture " + name).encode()
        (source / name).write_bytes(data)
        manifest["files"][name] = hashlib.sha256(data).hexdigest()
    (source / "inputs.json").write_text(json.dumps(manifest))
    return tmp_path / "store", source


def test_import_verifies_before_creating_vm_and_preserves_existing_data(bundle):
    store, source = bundle
    original = (source / "boot.img").read_bytes()
    (source / "boot.img").write_bytes(b"changed after manifest creation")
    with pytest.raises(ValueError, match="digest mismatch"):
        macos.import_bundle(store, "local-mac", source)
    assert not (store / "vms/local-mac").exists()
    (source / "boot.img").write_bytes(original)
    vm = macos.import_bundle(store, "local-mac", source)
    (vm / "disk.raw").write_bytes(b"user data")
    with pytest.raises(FileExistsError):
        macos.import_bundle(store, "local-mac", source)
    assert (vm / "disk.raw").read_bytes() == b"user data"
    assert vm.stat().st_mode & 0o777 == 0o700
    assert (vm / "agent-key").stat().st_mode & 0o777 == 0o600


def test_import_rejects_symlink_even_with_matching_content(bundle):
    store, source = bundle
    target = source.parent / "outside"
    (source / "boot.img").rename(target)
    (source / "boot.img").symlink_to(target)
    with pytest.raises(ValueError, match="Invalid bundle input"):
        macos.import_bundle(store, "local-mac", source)


@pytest.mark.parametrize("field,value", [("memory_mib", 9216), ("cpus", 8)])
def test_boot_geometry_cannot_be_changed_without_reassembly(bundle, field, value):
    store, source = bundle
    vm = macos.import_bundle(store, "local-mac", source)
    cfg = json.loads((vm / "config.json").read_text())
    cfg[field] = value
    with pytest.raises(ValueError, match="Reassemble"):
        config.validate(cfg)


def test_display_and_shared_memory_require_real_capabilities(bundle):
    store, source = bundle
    vm = macos.import_bundle(store, "local-mac", source)
    cfg = json.loads((vm / "config.json").read_text())
    snap = SimpleNamespace(
        runtime={"components": {"macos": {
            "features": ["vm-fd-share", "virtio-mmio", "pci-msi", "simplefb-wayland",
                         "wayland-scanout-blit"],
            "files": {"binary": "/usr/lib/gunyahvm/macos/crosvm/crosvm",
                      "renderer": "/usr/lib/gunyahvm/macos/renderer/lib.so",
                      "firmware": "/usr/lib/gunyahvm/macos/firmware/macos.fd"},
        }}},
        kernel_release="7.4.0-rc2-piano88", profile={"id": "piano"},
        protected_share=False,
        render_nodes=[{"driver": "msm", "accessible": True, "path": "/dev/dri/renderD128"}],
        udmabuf=True,
    )
    with pytest.raises(ValueError, match="VM-fd"):
        macos.build(vm, cfg, vm / "run", snap, "/run/user/1000/private/wayland")
    snap.protected_share = True
    with pytest.raises(ValueError, match="display socket"):
        macos.build(vm, cfg, vm / "run", snap, None)
    (vm / "firmware.fd").write_bytes(b"local firmware fixture")
    argv, env = macos.build(vm, cfg, vm / "run", snap, "/run/user/1000/private/wayland")
    assert "--virtio-mmio-gpu" in argv
    # The window shows the GPU's screen, where DroidVMDisplay presents; the boot framebuffer
    # stays without a window, and the VMM blits scanout frames into host-owned DMA-BUFs.
    assert "displays=[[mode=windowed[1280,800]," in argv[argv.index("--gpu") + 1]
    assert argv[argv.index("--wayland-sock") + 1] == "/run/user/1000/private/wayland"
    assert "wayland-socket" not in argv[argv.index("--simplefb") + 1]
    assert env["GPU_DISPLAY_COPY_MODE"] == "gpu"
    snap.render_nodes[0]["path"] = "/dev/dri/renderD129"
    _, env = macos.build(vm, cfg, vm / "run", snap, "/run/user/1000/private/wayland")
    assert env["CROSVM_GVM_BLIT_RENDER_NODE"] == "/dev/dri/renderD129"
    cfg["macos"]["display_copy_mode"] = "cpu"
    _, env = macos.build(vm, cfg, vm / "run", snap, "/run/user/1000/private/wayland")
    assert env["GPU_DISPLAY_COPY_MODE"] == "cpu"
    assert "CROSVM_GVM_BLIT_RENDER_NODE" not in env
    del cfg["macos"]["display_copy_mode"]
    features = snap.runtime["components"]["macos"]["features"]
    features.remove("wayland-scanout-blit")
    _, env = macos.build(vm, cfg, vm / "run", snap, "/run/user/1000/private/wayland")
    assert env["GPU_DISPLAY_COPY_MODE"] == "cpu"
    features.append("wayland-scanout-blit")
    snap.render_nodes = [{"driver": "msm", "accessible": False, "path": "/dev/dri/renderD129"}]
    _, env = macos.build(vm, cfg, vm / "run", snap, "/run/user/1000/private/wayland")
    assert env["GPU_DISPLAY_COPY_MODE"] == "cpu"
    assert "CROSVM_GVM_BLIT_RENDER_NODE" not in env
    # The boot disk first (the supervisor swaps in its run copy), then the system disk on PCI,
    # whose functions take MSI-X through GIC MBI SPIs.
    first = argv.index("--block")
    assert argv[first + 1] == str(vm / "boot.img") + ",ro=true"
    assert argv[argv.index("--block", first + 1) + 1] == str(vm / "disk.raw")
    assert argv[argv.index("--mbi-spis") + 1] == str(macos.MBI_SPIS)
    assert "--virtio-mmio-block" not in argv and "--virtio-mmio-net" not in argv
    snap.runtime["components"]["macos"]["features"].remove("pci-msi")  # a 1.5 VMM: no PCI MSI
    with pytest.raises(ValueError, match="runtime is not installed"):
        macos.build(vm, cfg, vm / "run", snap, "/run/user/1000/private/wayland")
    assert "--boost-uclamp" not in argv  # 1024 would not fit the performance cores
    cfg["macos"]["gpu"] = False  # without the GPU, the boot framebuffer is the window's screen
    snap.runtime["components"]["macos"]["features"].append("pci-msi")
    argv, env = macos.build(vm, cfg, vm / "run", snap, "/run/user/1000/private/wayland")
    assert "wayland-socket=/run/user/1000/private/wayland" in argv[argv.index("--simplefb") + 1]
    assert "--wayland-sock" not in argv and "--gpu" not in argv and "GPU_DISPLAY_COPY_MODE" not in env
    assert not any("vnc" in a.lower() for a in argv)
    cfg["display"]["vnc"] = True
    with pytest.raises(ValueError, match="embedded simplefb"):
        config.validate(cfg)


def test_poweroff_refusal_is_not_acknowledged(monkeypatch, tmp_path):
    monkeypatch.setattr(macos, "command", lambda *a: SimpleNamespace(
        returncode=1, stderr="rejected", stdout=""))
    with pytest.raises(ValueError, match="refused clean shutdown"):
        macos.poweroff(tmp_path, {}, "192.0.2.1")


@pytest.mark.parametrize("board,release", [
    ("piano", "7.4.0-rc2-piano-ramtest"),  # a RAM candidate kernel
    ("erhai", "7.5.0-rc1-erhai-local"),  # self-built: no erhai-kernel-image package
    ("unknown", "7.5.0"),
])
def test_macos_follows_kernel_capabilities_not_its_name(bundle, monkeypatch, board, release):
    store, source = bundle
    vm = macos.import_bundle(store, "local-mac", source)
    cfg = json.loads((vm / "config.json").read_text())
    monkeypatch.setattr(macos.subprocess, "run", lambda *a, **k: pytest.fail("package lookup"))
    snap = SimpleNamespace(
        runtime={"components": {"macos": {"features": ["vm-fd-share", "virtio-mmio", "pci-msi", "simplefb-wayland"]}}},
        kernel_release=release, profile={"id": board}, protected_share=True,
        render_nodes=[{"driver": "msm", "accessible": True}], udmabuf=True,
    )
    macos.require(snap, cfg)
    for field, value, reason in (("protected_share", None, "VM-fd"),
                                 ("render_nodes", [], "MSM render node"),
                                 ("udmabuf", False, "udmabuf")):
        missing = SimpleNamespace(**{**vars(snap), field: value})
        with pytest.raises(ValueError, match=reason):
            macos.require(missing, cfg)
    cfg["macos"]["gpu"] = False  # without the GPU profile neither the GPU nor udmabuf is needed
    macos.require(SimpleNamespace(**{**vars(snap), "render_nodes": [], "udmabuf": False}), cfg)


def test_boot_memory_error_distinguishes_rm_refusal(tmp_path):
    log = tmp_path / "crosvm.stderr"
    log.write_bytes(b"unrelated diagnostics\n" * 1000 +
                    b"GH-SHIM: gave up: the resource manager refused MEM_ACCEPT (error 0x9)\n")
    assert macos.boot_memory_error(log) == macos.BOOT_MEMORY_REFUSED
    log.write_bytes(b"GH-SHIM: gave up: the resource manager refused MEM_ACCEPT (error 0x1)\n")
    assert macos.boot_memory_error(log) is None
    log.write_bytes(b"Gunyah boot RAM preparation failed: Cannot allocate memory\n")
    assert macos.boot_memory_error(log) == macos.BOOT_MEMORY_UNAVAILABLE
    # The window's share can fail with ENOMEM too, before the shim accepts it.
    share = (b"GH-SHIM: before transferring lock ownership: VmLck:\t 9437188 kB\n"
             b"ERROR hypervisor::gunyah] GUNYAH-SHARE-BLOB: gpa=0x80600000 size=0x23f200000 flags=0x7 "
             b"FAILED after 1.27010176s: Cannot allocate memory (os error 12)\n"
             b"ERROR crosvm] exiting with error 1: the architecture failed to build the vm\n")
    log.write_bytes(share)
    assert macos.boot_memory_error(log) == macos.BOOT_MEMORY_REFUSED
    # A runtime blob share that fails later is not the boot window, with or without the boot lines.
    blob = (b"ERROR hypervisor::gunyah] GUNYAH-SHARE-BLOB: gpa=0x2c0800000 size=0x200000 flags=0x3 "
            b"FAILED after 1ms: Cannot allocate memory (os error 12)\n")
    log.write_bytes(blob)
    assert macos.boot_memory_error(log) is None
    log.write_bytes(share.split(b"ERROR hypervisor")[0] +
                    b"GH-SHIM: window 0x80600000+0x23f200000 shared as 1 parcel(s) in 43ms\n" + blob)
    assert macos.boot_memory_error(log) is None


def test_reimport_cannot_share_control_identity(bundle):
    store, source = bundle
    macos.import_bundle(store, "first", source)
    with pytest.raises(ValueError, match="already belongs"):
        macos.import_bundle(store, "second", source)
    assert not (store / "vms/second").exists()


def test_generated_keys_are_independent_and_private(tmp_path):
    a = macos.create_control_identity(tmp_path / "one")
    b = macos.create_control_identity(tmp_path / "two")
    assert macos.control_public_key(a) != macos.control_public_key(b)
    assert a.stat().st_mode & 0o777 == 0o600
    assert a.parent.stat().st_mode & 0o777 == 0o700
    before = a.read_bytes()
    with pytest.raises(FileExistsError):
        macos.create_control_identity(a.parent)
    assert a.read_bytes() == before


def test_portable_bundle_imports_local_firmware(bundle):
    store, source = bundle
    manifest = json.loads((source / "inputs.json").read_text())
    manifest["schema"] = 3
    data = b"local firmware fixture"
    (source / "firmware.fd").write_bytes(data)
    manifest["files"]["firmware.fd"] = hashlib.sha256(data).hexdigest()
    (source / "inputs.json").write_text(json.dumps(manifest))
    vm = macos.import_bundle(store, "portable", source)
    assert (vm / "firmware.fd").read_bytes() == data
    assert macos.local_firmware(vm, {}) == vm / "firmware.fd"


def test_macos_takes_a_free_subnet_beside_a_vm_on_its_own(bundle):
    from gunyahvm import core
    store, source = bundle
    vm = macos.import_bundle(store, "local-mac", source)
    cfg = json.loads((vm / "config.json").read_text())
    # The macOS seed configures no network: the guest uses DHCP on whatever
    # subnet the host serves, and control finds it by MAC on the TAP.
    running = {"alpha": ({"network_mode": "nat", "subnet": cfg["subnet"]}, {})}
    change = core.resolve_subnet(store, vm, cfg, running)
    stored = json.loads((vm / "config.json").read_text())
    assert change == {"from": running["alpha"][0]["subnet"], "because": "alpha", "to": stored["subnet"]}
    assert stored["subnet"] != running["alpha"][0]["subnet"] and stored["mac"] == cfg["mac"]
    config.validate(stored)
