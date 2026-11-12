# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Exercise disk contents, failed copies, live-VM refusal and startup locking."""

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from gunyahvm import core, snapshots, storage
from test_core_boundaries import vm_config


@pytest.fixture
def machine(tmp_path, monkeypatch):
    monkeypatch.setattr(core.pwd, "getpwuid", lambda _: SimpleNamespace(pw_dir=str(tmp_path)))
    store = tmp_path / "store"
    vm = store / "vms/test"
    vm.mkdir(parents=True, mode=0o700)
    cfg = vm_config()
    cfg["ssh_public_key"] = "ssh-ed25519 fixture"
    cfg["ssh_identity"] = str(vm / "agent-key")
    storage.atomic_json(vm / "config.json", cfg)
    storage.atomic_json(vm / "state.json", {"state": "stopped", "clean_shutdown": True})
    (vm / "boot").mkdir()
    for path in ["boot/Image", "boot/initrd.img", "boot/cmdline", "seed.iso", "agent-key"]:
        (vm / path).write_text("fixture")
    with (vm / "disk.raw").open("wb") as disk:
        disk.write(b"start")
        disk.seek(4 * 1024**2)
        disk.write(b"end")
    return store, vm


def test_snapshot_restore_keeps_source_until_success(machine):
    store, vm = machine
    before = (vm / "disk.raw").read_bytes()
    snapshots.take(store, "test", "baseline")
    with (vm / "disk.raw").open("r+b") as stream:
        stream.write(b"newer")
    assert snapshots.list_snapshots(store, "test")[0]["name"] == "baseline"
    snapshots.restore(store, "test", "baseline")
    assert (vm / "disk.raw").read_bytes() == before
    assert (vm / "snapshots/baseline").stat().st_mode & 0o777 == 0o700
    assert (vm / "snapshots/baseline/disk.raw").stat().st_blocks * 512 < len(before) // 2


def test_clone_gets_independent_disk_identity_and_control_paths(machine):
    store, vm = machine
    (vm / "runs").mkdir()
    (vm / "runs/not-a-guest-input").write_text("private runtime state")
    new = snapshots.clone(store, "test", "copy")
    old_cfg = storage.read_json(vm / "config.json")
    cfg = storage.read_json(new / "config.json")
    assert cfg["id"] != old_cfg["id"] and cfg["mac"] != old_cfg["mac"]
    assert cfg["ssh_identity"] == str(new / "agent-key")
    assert not (new / "runs").exists()
    assert (new / "disk.raw").read_bytes() == (vm / "disk.raw").read_bytes()
    with (new / "disk.raw").open("r+b") as stream:
        stream.write(b"clone")
    assert (vm / "disk.raw").read_bytes().startswith(b"start")
    with pytest.raises(core.Failure, match="already exists"):
        snapshots.clone(store, "test", "copy")


def test_clone_materializes_managed_boot_generation(machine):
    store, vm = machine
    (vm / "boot").rename(vm / "boot-generation")
    (vm / "boot").symlink_to("boot-generation")
    new = snapshots.clone(store, "test", "copy")
    assert not (new / "boot").is_symlink()
    assert (new / "boot/cmdline").read_text() == "fixture"


def test_live_vm_and_installation_are_refused(machine, monkeypatch):
    store, vm = machine
    monkeypatch.setattr(core, "vmm_active", lambda _: True)
    for operation in (snapshots.take, snapshots.clone):
        with pytest.raises(core.Failure, match="Shut down"):
            operation(store, "test", "copy")
    monkeypatch.setattr(core, "vmm_active", lambda _: False)
    cfg = storage.read_json(vm / "config.json")
    cfg["install_media"] = True
    storage.atomic_json(vm / "config.json", cfg)
    with pytest.raises(core.Failure, match="Finish installing"):
        snapshots.take(store, "test", "copy")


def test_copy_failure_cannot_publish_or_replace_disk(machine, monkeypatch):
    store, vm = machine
    snapshots.take(store, "test", "baseline")
    before = (vm / "disk.raw").read_bytes()

    def fail(_source, dest):
        dest.write_bytes(b"incomplete")
        raise OSError("disk full")

    monkeypatch.setattr(snapshots, "copy_file", fail)
    for operation, label in [(snapshots.take, "failed"), (snapshots.restore, "baseline"), (snapshots.clone, "failed")]:
        with pytest.raises(OSError, match="disk full"):
            operation(store, "test", label)
    assert (vm / "disk.raw").read_bytes() == before
    assert [p.name for p in (vm / "snapshots").iterdir()] == ["baseline"]
    assert not (store / "vms/failed").exists()


def test_disk_operation_excludes_startup_and_settings(machine, monkeypatch):
    store, vm = machine
    monkeypatch.setattr(core, "host_snapshot", lambda: pytest.fail("startup passed disk lock"))
    with storage.operation_lock(vm):
        with pytest.raises(core.Failure, match="Another operation"):
            core.start_vm_authorized(store, "test")
        with pytest.raises(core.Failure, match="Another operation"):
            core.save_config(store, "test", storage.read_json(vm / "config.json"))
        with pytest.raises(core.Failure, match="Another operation"):
            snapshots.take(store, "test", "copy")


def test_snapshot_symlinks_and_truncation_cannot_restore(machine, tmp_path):
    store, vm = machine
    snapshots.take(store, "test", "baseline")
    disk = vm / "snapshots/baseline/disk.raw"
    original = (vm / "disk.raw").read_bytes()
    disk.write_bytes(b"truncated")
    with pytest.raises(core.Failure, match="incomplete"):
        snapshots.restore(store, "test", "baseline")
    disk.unlink()
    disk.symlink_to(vm / "disk.raw")
    with pytest.raises(OSError):
        snapshots.restore(store, "test", "baseline")
    assert (vm / "disk.raw").read_bytes() == original
