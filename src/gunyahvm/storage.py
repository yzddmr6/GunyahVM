# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# SafeOpen adapted from iceland-vms (mzakamin-hub)
# d628b9b25d08e074b34a2f1a6113ab23767b78d7; atomic storage from GunyahVM.
"""Atomic per-user files; adapted from GunyahVM and iceland-vms SafeOpen."""

import json
import os
from pathlib import Path
import stat
import tempfile
import uuid
from contextlib import contextmanager
import fcntl

from .errors import Failure


def display_socket(vm_id):
    return Path(f"/run/user/{os.getuid()}") / ("gunyahvm-display-" + str(uuid.UUID(vm_id))) / "wayland"


@contextmanager
def operation_lock(vm):
    """Serialize disk operations with startup and configuration changes."""
    path = Path(vm) / ".operation.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise Failure("Invalid VM operation lock")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise Failure("Another operation is using this virtual machine") from exc
        yield
    finally:
        os.close(fd)


def open_nofollow(path, flags, owner=None):
    # O_NONBLOCK prevents a malicious FIFO from hanging the caller before fstat.
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or (owner is not None and info.st_uid != owner):
        os.close(fd)
        raise Failure("not an owned regular file: " + str(path))
    return fd


def atomic_json(path, data):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path):
    with os.fdopen(open_nofollow(path, os.O_RDONLY, os.getuid())) as stream:
        return json.load(stream)
