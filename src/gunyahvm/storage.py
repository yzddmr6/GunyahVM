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

from .errors import Failure


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
