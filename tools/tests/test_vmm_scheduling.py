# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Host CPU frequency follows the guest: every VMM thread carries a utilization floor."""

import ctypes
import errno
import os
import struct
import subprocess
import sys

import pytest

from gunyahvm import crosvmargs, host, macos_helper
from test_capabilities import capable
from test_core_boundaries import vm_config

GETATTR = {"aarch64": 275, "x86_64": 315}

# The child reports the floor of its main thread and of a thread it starts later,
# as a VMM's GPU and device threads are started after it is spawned.
CHILD = r"""
import ctypes, os, struct, threading
libc = ctypes.CDLL(None, use_errno=True)
def floor():
    out = ctypes.create_string_buffer(56)
    assert libc.syscall(@NR@, 0, out, 56, 0) == 0
    return struct.unpack("=IIQiIQQQII", out.raw)[8]
seen = [floor()]
worker = threading.Thread(target=lambda: seen.append(floor()))
worker.start(); worker.join()
print(*seen)
"""


def own_floor():
    libc = ctypes.CDLL(None, use_errno=True)
    out = ctypes.create_string_buffer(56)
    assert libc.syscall(GETATTR[os.uname().machine], 0, out, 56, 0) == 0
    return struct.unpack("=IIQiIQQQII", out.raw)[8]


def spawn_child():
    program = CHILD.replace("@NR@", str(GETATTR[os.uname().machine]))
    return subprocess.run([sys.executable, "-c", program], capture_output=True, text=True,
                          check=True).stdout.split()


@pytest.fixture
def clamping():
    if os.uname().machine not in GETATTR or not host.set_uclamp_min(0):
        pytest.skip("this kernel has no utilization clamping")


def test_vmm_threads_inherit_the_floor_and_the_supervisor_keeps_none(clamping):
    with host.uclamp_floor() as floor:
        child = spawn_child()
    assert floor == host.VMM_UCLAMP_MIN
    assert child == [str(host.VMM_UCLAMP_MIN)] * 2  # main thread and a later thread
    assert own_floor() == 0


def test_kernels_without_utilization_clamping_change_nothing(clamping, monkeypatch):
    # The 1.6.1 tablet kernels are built without CONFIG_UCLAMP_TASK.
    def refuse(raw):
        raise OSError(errno.EOPNOTSUPP, os.strerror(errno.EOPNOTSUPP))

    monkeypatch.setattr(host, "_sched_setattr", refuse)
    with host.uclamp_floor() as floor:
        child = spawn_child()
    assert floor is None and child == ["0", "0"] and own_floor() == 0


def test_both_vmms_and_the_assembly_helper_boost_their_vcpus(tmp_path):
    (tmp_path / "boot").mkdir()
    (tmp_path / "boot/cmdline").write_text("root=LABEL=cloudimg-rootfs ro")
    # Linux and Windows share this argument list; macOS's is checked with its profile.
    argv, _ = crosvmargs.build(tmp_path, vm_config(), tmp_path / "run", capable(), "/run/user/1000/w")
    assert "--boost-uclamp" in argv
    runtime = {"components": {"crosvm": {"files": {"binary": "/crosvm"}},
                              "guest_kernel": {"files": {"image": "/Image"}}}}
    helper = macos_helper.argv(runtime, tmp_path, {"disks": []}, tmp_path / "initrd", tmp_path / "log")
    assert "--boost-uclamp" in helper
