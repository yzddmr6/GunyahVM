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


def cpus(tmp_path, capacities, offline=()):
    root = tmp_path / "root"
    for index, capacity in enumerate(capacities):
        cpu = root / f"sys/devices/system/cpu/cpu{index}"
        cpu.mkdir(parents=True)
        if capacity is not None:
            (cpu / "cpu_capacity").write_text(f"{capacity}\n")
        if index:  # cpu0 has no online switch
            (cpu / "online").write_text("0\n" if index in offline else "1\n")
    return root


@pytest.mark.parametrize("capacities", [
    [792] * 6 + [1024] * 2,  # SM8750: performance cores (CPU0-5) and two prime cores
    [837] * 6 + [1024] * 2,
    [1024] * 8,
    [160, 160, 160, 160, 512, 512, 1024],
])
def test_the_floor_never_reaches_the_smallest_cores_capacity(tmp_path, capacities):
    # Above a core's capacity the scheduler counts the thread as not fitting it
    # (util_fits_cpu() is -1) and crowds the VMM's threads onto the largest cores.
    floor = host.vmm_uclamp_min(cpus(tmp_path, capacities))
    assert 0 < floor < min(capacities)
    assert floor == int(min(capacities) * host.VMM_UCLAMP_RATIO)


def test_offline_cores_do_not_count_and_an_unknown_capacity_sets_no_floor(tmp_path):
    assert host.vmm_uclamp_min(cpus(tmp_path / "a", [1024, 400, 1024], offline=[1])) == int(1024 * host.VMM_UCLAMP_RATIO)
    assert host.vmm_uclamp_min(cpus(tmp_path / "b", [792, None, 1024])) is None
    assert host.vmm_uclamp_min(tmp_path / "none") is None


def test_vmm_threads_inherit_the_floor_and_the_supervisor_keeps_none(clamping):
    with host.uclamp_floor(512) as floor:
        child = spawn_child()
    assert floor == 512 and child == ["512", "512"]  # main thread and a later thread
    assert own_floor() == 0
    # By default the floor comes from this host's cores; none without their capacity.
    with host.uclamp_floor() as floor:
        child = spawn_child()
    assert floor == host.vmm_uclamp_min() and child == [str(floor or 0)] * 2
    assert own_floor() == 0


def test_kernels_without_utilization_clamping_change_nothing(clamping, monkeypatch):
    # The 1.6.1 tablet kernels are built without CONFIG_UCLAMP_TASK.
    def refuse(raw):
        raise OSError(errno.EOPNOTSUPP, os.strerror(errno.EOPNOTSUPP))

    monkeypatch.setattr(host, "_sched_setattr", refuse)
    with host.uclamp_floor() as floor:
        child = spawn_child()
    assert floor is None and child == ["0", "0"] and own_floor() == 0


def test_no_vmm_pins_its_vcpus_at_full_utilization(tmp_path):
    # crosvm's --boost-uclamp sets 1024, above the capacity of all but the
    # largest cores; the inherited floor covers vCPU threads instead.
    (tmp_path / "boot").mkdir()
    (tmp_path / "boot/cmdline").write_text("root=LABEL=cloudimg-rootfs ro")
    # Linux and Windows share this argument list; macOS's is checked with its profile.
    argv, _ = crosvmargs.build(tmp_path, vm_config(), tmp_path / "run", capable(), "/run/user/1000/w")
    assert "--boost-uclamp" not in argv
    runtime = {"components": {"crosvm": {"files": {"binary": "/crosvm"}},
                              "guest_kernel": {"files": {"image": "/Image"}}}}
    helper = macos_helper.argv(runtime, tmp_path, {"disks": []}, tmp_path / "initrd", tmp_path / "log")
    assert "--boost-uclamp" not in helper
