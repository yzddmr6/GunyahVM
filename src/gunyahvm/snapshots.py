# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Cold disk snapshots and independent VM clones, with atomic publication."""

from contextlib import contextmanager
from copy import deepcopy
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
import time
import uuid

from . import config, core, storage
from .errors import Failure


@contextmanager
def stopped(store, name):
    vm, cfg = core.owned_vm(store, name, os.getuid(), inputs=False)
    with storage.operation_lock(vm):
        state = core.status(vm, cfg)
        if state["state"] in ("starting", "running", "restarting", "stopping") or core.monitor_active(state) or core.vmm_active(state):
            raise Failure("Shut down the virtual machine before changing its disks")
        if cfg.get("install_media"):
            raise Failure("Finish installing the guest before taking snapshots or cloning")
        yield vm, cfg, state


def private_directory(path):
    path.mkdir(mode=0o700, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise Failure("Snapshot directory is not private to its owner")
    return path


def copy_file(source, destination):
    with os.fdopen(storage.open_nofollow(source, os.O_RDONLY, os.getuid()), "rb") as stream:
        info = os.fstat(stream.fileno())
        if shutil.disk_usage(destination.parent).free < info.st_blocks * 512 + 64 * 1024**2:
            raise Failure("Not enough free space for the disk copy")
        subprocess.run([
            "cp", "--reflink=auto", "--sparse=always", "--", "/proc/self/fd/" + str(stream.fileno()), str(destination),
        ], pass_fds=(stream.fileno(),), check=True)
        destination.chmod(0o600)
        with destination.open("rb") as copied:
            os.fsync(copied.fileno())
        after = os.fstat(stream.fileno())
        if (info.st_size, info.st_mtime_ns, info.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
            raise Failure("The source disk changed during the copy")
        if destination.stat().st_size != info.st_size:
            raise Failure("The copied disk has the wrong size")


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def take(store, name, label):
    label = core.vm_name(label)
    with stopped(store, name) as (vm, cfg, state):
        folder = private_directory(vm / "snapshots")
        final = folder / label
        if final.exists() or final.is_symlink():
            raise Failure("A snapshot with this name already exists")
        with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=folder) as temporary:
            prepared = Path(temporary) / label
            prepared.mkdir(mode=0o700)
            copy_file(vm / "disk.raw", prepared / "disk.raw")
            metadata = {"schema": 1, "name": label, "vm_id": cfg["id"], "os": cfg["os"],
                        "created_at": time.time(), "size": (prepared / "disk.raw").stat().st_size,
                        "clean_shutdown": state.get("clean_shutdown"), "config": cfg}
            storage.atomic_json(prepared / "snapshot.json", metadata)
            prepared.rename(final)
            sync_directory(folder)
        return metadata


def read(vm, name):
    private_directory(vm / "snapshots")
    folder = vm / "snapshots" / core.vm_name(name)
    if folder.is_symlink() or not folder.is_dir():
        raise Failure("Snapshot is missing or invalid")
    private_directory(folder)
    metadata = storage.read_json(folder / "snapshot.json")
    if metadata.get("schema") != 1 or metadata.get("name") != name:
        raise Failure("Invalid snapshot metadata")
    with os.fdopen(storage.open_nofollow(folder / "disk.raw", os.O_RDONLY, os.getuid())) as stream:
        if os.fstat(stream.fileno()).st_size != metadata.get("size"):
            raise Failure("Snapshot disk is incomplete")
    return folder, metadata


def list_snapshots(store, name):
    vm, _ = core.owned_vm(store, name, os.getuid(), inputs=False)
    folder = vm / "snapshots"
    if not folder.exists():
        return []
    private_directory(folder)
    return [read(vm, p.name)[1] for p in sorted(folder.iterdir()) if not p.name.startswith(".")]


def restore(store, name, snapshot):
    with stopped(store, name) as (vm, cfg, state):
        folder, metadata = read(vm, snapshot)
        if metadata["vm_id"] != cfg["id"] or metadata["os"] != cfg["os"] or metadata["size"] != (vm / "disk.raw").stat().st_size:
            raise Failure("Snapshot belongs to a different virtual machine or disk size")
        with tempfile.TemporaryDirectory(prefix=".restore-", dir=vm) as temporary:
            disk = Path(temporary) / "disk.raw"
            copy_file(folder / "disk.raw", disk)
            os.replace(disk, vm / "disk.raw")
            sync_directory(vm)
        state.update(state="stopped", clean_shutdown=metadata.get("clean_shutdown"), restored_snapshot=snapshot)
        storage.atomic_json(vm / "state.json", state)


def delete(store, name, snapshot):
    with stopped(store, name) as (vm, _cfg, _state):
        folder, _metadata = read(vm, snapshot)
        shutil.rmtree(folder)
        sync_directory(folder.parent)


def clone(store, name, new_name):
    new_name = core.vm_name(new_name)
    with stopped(store, name) as (vm, cfg, state):
        core.owned_vm(store, name, os.getuid(), inputs=True)
        target = vm.parent / new_name
        if target.exists() or target.is_symlink():
            raise Failure("A virtual machine with this name already exists")
        cfg = deepcopy(cfg)
        cfg.update(id=str(uuid.uuid4()), name=new_name)
        cfg = config.complete_new(cfg)
        if cfg.get("ssh_identity") and Path(cfg["ssh_identity"]).is_relative_to(vm):
            cfg["ssh_identity"] = str(target / Path(cfg["ssh_identity"]).relative_to(vm))
        config.validate(cfg)
        with tempfile.TemporaryDirectory(prefix=".clone-", dir=vm.parent) as temporary:
            prepared = Path(temporary) / new_name
            prepared.mkdir(mode=0o700)
            # Copy guest inputs only: never logs, snapshots, sockets, shared
            # host directories or the source VM's runtime/process identity.
            inputs = ("disk.raw", "seed.iso", "firmware.fd", "agent-key", "agent-key.pub")
            for item in inputs:
                source = vm / item
                if source.exists() or source.is_symlink():
                    copy_file(source, prepared / item)
            boot = vm / "boot"
            if boot.exists():
                # Managed cloud images atomically select an extracted boot
                # generation through an in-VM symlink. Materialize that safe
                # generation in the clone; never follow an external target.
                if not boot.resolve().is_relative_to(vm.resolve()):
                    raise Failure("Invalid boot directory")
                (prepared / "boot").mkdir(mode=0o700)
                for source in boot.iterdir():
                    copy_file(source, prepared / "boot" / source.name)
            storage.atomic_json(prepared / "config.json", cfg)
            storage.atomic_json(prepared / "state.json", {"state": "created", "clean_shutdown": state.get("clean_shutdown")})
            prepared.rename(target)
            sync_directory(vm.parent)
        return target
