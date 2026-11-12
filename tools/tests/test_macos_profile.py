# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Local-input and boot-ABI boundaries; no Apple files or hardware required."""

import hashlib
import json
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
            "features": ["vm-fd-share", "virtio-mmio", "simplefb-wayland"],
            "files": {"binary": "/usr/lib/gunyahvm/macos/crosvm/crosvm",
                      "renderer": "/usr/lib/gunyahvm/macos/renderer/lib.so",
                      "firmware": "/usr/lib/gunyahvm/macos/firmware/macos.fd"},
        }}},
        protected_share=False,
        render_nodes=[{"driver": "msm", "accessible": True}],
    )
    with pytest.raises(ValueError, match="VM-fd"):
        macos.build(vm, cfg, vm / "run", snap, "/run/user/1000/private/wayland")
    snap.protected_share = True
    with pytest.raises(ValueError, match="display socket"):
        macos.build(vm, cfg, vm / "run", snap, None)
    argv, _ = macos.build(vm, cfg, vm / "run", snap, "/run/user/1000/private/wayland")
    assert "--virtio-mmio-gpu" in argv
    assert "wayland-socket=/run/user/1000/private/wayland" in argv[argv.index("--simplefb") + 1]
    assert "--wayland-sock" not in argv  # Not a guest virtio-wl forwarding device.
    assert not any("vnc" in a.lower() for a in argv)
    cfg["display"]["vnc"] = True
    with pytest.raises(ValueError, match="embedded simplefb"):
        config.validate(cfg)


def test_poweroff_refusal_is_not_acknowledged(monkeypatch, tmp_path):
    monkeypatch.setattr(macos, "command", lambda *a: SimpleNamespace(
        returncode=1, stderr="rejected", stdout=""))
    with pytest.raises(ValueError, match="refused clean shutdown"):
        macos.poweroff(tmp_path, {}, "192.0.2.1")
