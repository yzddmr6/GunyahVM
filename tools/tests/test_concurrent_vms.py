# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Several VMs at once.

The start, admission, supervisor and stop paths are the real ones, in real
processes; a VMM stand-in carries the command line GunyahVM gives crosvm.
Only hardware, privilege, logind and host networking are substituted.
"""

import ipaddress
import json
import os
import signal
import socket
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from gunyahvm import config, core, host, snapshots, storage
from gunyahvm.ui import text

SRC = Path(__file__).resolve().parents[2] / "src"
TESTS = Path(__file__).resolve().parent

GUEST = r"""
import json, os, sys, time
from pathlib import Path
rd = Path(sys.argv[1])
with (rd / "agent.log").open("a") as log:
    log.write("GUNYAHVM_READY " + json.dumps({"ipv4": [sys.argv[2]], "boot_id": "guest",
              "setup": {"status": "done", "problems": []}}) + "\n")
fd = os.open(rd / "agent.in", os.O_RDONLY | os.O_NONBLOCK)
deadline = time.monotonic() + 60
while time.monotonic() < deadline:
    try:
        command = os.read(fd, 1024)
    except BlockingIOError:
        command = b""
    if b"poweroff" in command:
        with (rd / "agent.log").open("a") as log:
            log.write("GUNYAHVM_POWEROFF_REQUEST\n")
        raise SystemExit(0)
    time.sleep(0.02)
raise SystemExit(7)
"""

# Runs as supervisor.py, in its own process like the real one.
HARNESS = r"""
import ipaddress, json, os, sys
from pathlib import Path
from types import SimpleNamespace
sys.path[:0] = [@SRC@, @TESTS@]
from gunyahvm import core, host
from test_capabilities import capable
fixture = json.loads(Path(@FIXTURE@).read_text())
action, store, name = sys.argv[1:4]
core.pwd.getpwuid = lambda uid: SimpleNamespace(pw_dir=fixture["home"])
core.access_limits = lambda cfg, check_vhost=True: (1 << 40, 1 << 40)
core.seed = lambda *args: None
core.capabilities.sensors = lambda *args, **kwargs: {"batteries": [], "temperatures": []}


def note(line):
    with open(fixture["events"], "a") as stream:
        stream.write(line + "\n")


def snapshot():
    snap = capable()
    snap.memory = {"MemTotal": 16384, "MemAvailable": fixture["available"]}
    snap.commitments = host.guest_commitments()
    return snap


class Guard:
    def __init__(self, stop):
        note("inhibit " + name)

    def close(self):
        note("release " + name)


def build(vm, cfg, rd, snap, wayland):
    guest = ipaddress.ip_network(cfg["subnet"]).network_address + 2
    return [sys.executable, "-c", fixture["guest"], str(rd), str(guest), "run",
            "--hypervisor", "gunyah", "--mem", str(cfg["memory_mib"]),
            "--name", "gunyahvm-" + cfg["name"]], {}


core.host_snapshot = snapshot
core.SleepGuard = Guard
def network(action, cfg):
    note(action + " " + cfg["name"])
    note("subnet " + cfg["name"] + " " + cfg["subnet"])


core.network_action = network
core.crosvmargs.build = build
vm, cfg = core.owned_vm(Path(store), name, os.getuid())
sys.exit(core.supervise(vm, cfg, *[int(fd) for fd in sys.argv[4:]]))
"""


# Read before the library fixture makes os.uname() report an aarch64 host.
CLAMPING = host.set_uclamp_min(0)
FLOOR = host.vmm_uclamp_min()
GETATTR = {"aarch64": 275, "x86_64": 315}.get(os.uname().machine)


def floor_of(pid):
    import ctypes
    import struct
    if not CLAMPING:
        return 0
    out = ctypes.create_string_buffer(56)
    assert ctypes.CDLL(None, use_errno=True).syscall(GETATTR, pid, out, 56, 0) == 0
    return struct.unpack("=IIQiIQQQII", out.raw)[8]


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A VM library, a host with AVAILABLE MiB free, and the events both sides record."""
    store = tmp_path / "store"
    (store / "vms").mkdir(parents=True)
    events = tmp_path / "events"
    events.touch()
    fixture = {"home": str(tmp_path), "events": str(events), "available": 8192, "guest": GUEST}
    (tmp_path / "fixture.json").write_text(json.dumps(fixture))
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "supervisor.py").write_text(
        HARNESS.replace("@SRC@", repr(str(SRC))).replace("@TESTS@", repr(str(TESTS)))
        .replace("@FIXTURE@", repr(str(tmp_path / "fixture.json"))))
    (tmp_path / "thp").write_text("always [advise] never\n")
    (tmp_path / "runtime").mkdir(mode=0o700)

    def note(line):
        with events.open("a") as stream:
            stream.write(line + "\n")

    def snapshot():
        from test_capabilities import capable
        snap = capable()
        snap.memory = {"MemTotal": 16384, "MemAvailable": fixture["available"]}
        snap.commitments = host.guest_commitments()
        return snap

    monkeypatch.setattr(core.pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(tmp_path)))
    monkeypatch.setattr(core.os, "uname", lambda: SimpleNamespace(machine="aarch64"))
    monkeypatch.setattr(core, "host_snapshot", snapshot)
    monkeypatch.setattr(core, "access_limits", lambda cfg, check_vhost=True: (1 << 40, 1 << 40))
    monkeypatch.setattr(core, "THP", tmp_path / "thp")
    def network(action, cfg):
        note(action + " " + cfg["name"])
        note("subnet " + cfg["name"] + " " + cfg["subnet"])

    monkeypatch.setattr(core, "network_action", network)
    monkeypatch.setattr(core, "BUNDLE", bundle)
    monkeypatch.setattr(storage, "runtime_dir", lambda: tmp_path / "runtime", raising=False)
    def add(name, memory_mib=2048, subnet=None, forwards=()):
        ident = str(__import__("uuid").uuid4())
        cfg = config.complete_new({
            "id": ident, "uid": os.getuid(), "gid": os.getgid(), "name": name, "os": "linux",
            "cpus": 2, "memory_mib": memory_mib, "disk_gib": 8,
            "subnet": subnet or config.free_subnet(ident, core.library_subnets(store)),
            "vhost_net": False, "username": "ubuntu", "boot_managed": False,
            "ssh_public_key": "ssh-ed25519 fixture",
        })
        cfg["forwards"] = [{"proto": "tcp", "host": port, "guest": 22} for port in forwards]
        vm = store / "vms" / name
        (vm / "boot").mkdir(parents=True, mode=0o700)
        for item in ("disk.raw", "seed.iso", "boot/Image", "boot/initrd.img", "boot/cmdline"):
            (vm / item).write_text("fixture")
        storage.atomic_json(vm / "config.json", config.validate(cfg))
        storage.atomic_json(vm / "state.json", {"state": "created"})
        return vm, cfg

    def start(name):
        core._start_vm_authorized(store, name)

    def state(name):
        return storage.read_json(store / "vms" / name / "state.json")

    def wait(name, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = state(name)
            if predicate(current):
                return current
            time.sleep(0.05)
        raise AssertionError(f"{name}: {state(name)}")

    def lines():
        return events.read_text().split("\n")[:-1]

    yield SimpleNamespace(store=store, add=add, start=start, state=state, wait=wait,
                          events=lines, fixture=fixture)
    # Never leave a supervisor or VMM stand-in behind: the next test's
    # admission would count it. Each has this library's path in its argv.
    for entry in Path("/proc").glob("[0-9]*"):
        try:
            if str(store).encode() in (entry / "cmdline").read_bytes() and int(entry.name) != os.getpid():
                os.kill(int(entry.name), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def test_two_vms_run_side_by_side_and_stop_independently(library):
    port = free_port()
    alpha, alpha_cfg = library.add("alpha", forwards=[port])
    beta, beta_cfg = library.add("beta", forwards=[port])
    assert alpha_cfg["subnet"] != beta_cfg["subnet"]
    assert alpha_cfg["tap"] != beta_cfg["tap"]
    assert storage.display_socket(alpha_cfg["id"]) != storage.display_socket(beta_cfg["id"])
    library.start("alpha")
    library.wait("alpha", lambda s: s.get("guest_ready"))
    library.start("beta")
    ready = library.wait("beta", lambda s: s.get("guest_ready"))
    first = library.state("alpha")
    assert first["state"] == ready["state"] == "running"
    assert core.vmm_active(first) and core.vmm_active(ready)
    assert first["supervisor_pid"] != ready["supervisor_pid"] and first["pid"] != ready["pid"]
    running = {c["name"]: c for c in host.guest_commitments()}
    assert running["alpha"]["pid"] == first["pid"] and running["beta"]["pid"] == ready["pid"]
    # Each VMM keeps its utilization floor where this kernel clamps utilization.
    for state in (first, ready):
        assert floor_of(state["pid"]) == state.get("uclamp_min", 0)
        assert state.get("uclamp_min") == (FLOOR if CLAMPING else None)
    # One host port has one listener: the second VM says so instead of losing it silently.
    assert "forward_errors" not in first
    assert ready["forward_errors"] and str(port) in ready["forward_errors"][0]

    stopped = core.stop_vm(alpha, alpha_cfg)
    assert stopped["state"] == "stopped" and stopped["clean_shutdown"]
    still = library.state("beta")
    assert still["state"] == "running" and core.vmm_active(still) and core.monitor_active(still)
    assert [c["name"] for c in host.guest_commitments()] == ["beta"]
    assert core.stop_vm(beta, beta_cfg)["clean_shutdown"]
    events = library.events()
    assert events.index("up alpha") < events.index("up beta") < events.index("down alpha")
    assert events.index("down alpha") < events.index("down beta")
    for name in ("alpha", "beta"):
        assert events.index("inhibit " + name) < events.index("release " + name)
        assert events.count("up " + name) == events.count("down " + name) == 1


def test_second_vm_is_refused_when_memory_is_short_and_says_why(library):
    alpha, alpha_cfg = library.add("alpha", memory_mib=6144)
    library.add("beta", memory_mib=4096)
    library.start("alpha")
    library.wait("alpha", lambda s: s.get("guest_ready"))
    # MemAvailable (8192 MiB) does not include the 6 GiB alpha has not touched
    # yet; Gunyah pins it on first use, so beta must not count on it.
    with pytest.raises((core.Failure, ValueError)) as refused:
        library.start("beta")
    message = str(refused.value)
    assert message.startswith("Need 4096 MiB available host memory; ")
    assert "running virtual machines (alpha) will still take" in message
    explained = text.explain(message)
    assert "4 GB needed" in explained and "Shut one down" in explained
    assert library.state("beta") == {"state": "created"}
    assert "up beta" not in library.events()
    assert library.state("alpha")["state"] == "running"
    core.stop_vm(alpha, alpha_cfg)
    library.start("beta")  # the memory is back once alpha has stopped
    library.wait("beta", lambda s: s.get("guest_ready"))


def test_a_vm_on_a_running_vms_subnet_moves_to_a_free_one(library):
    # A clone made before 1.5.0 kept its source's subnet; both now run.
    alpha, alpha_cfg = library.add("alpha", forwards=[free_port()])
    beta, beta_cfg = library.add("beta", subnet=alpha_cfg["subnet"], forwards=[free_port()])
    library.start("alpha")
    library.wait("alpha", lambda s: s.get("guest_ready"))
    library.start("beta")
    moved = storage.read_json(beta / "config.json")["subnet"]
    assert moved not in (alpha_cfg["subnet"], beta_cfg["subnet"])
    assert moved == config.free_subnet(beta_cfg["id"], core.library_subnets(library.store, "beta"))
    # The host network, the guest address, its forwards and the control
    # channel all follow the new subnet.
    assert "subnet beta " + moved in library.events()
    ready = library.wait("beta", lambda s: s.get("guest_ready"))
    assert ipaddress.ip_address(ready["ipv4"]) in ipaddress.ip_network(moved)
    assert "forward_errors" not in ready
    assert ready["subnet_changed"] == {"from": alpha_cfg["subnet"], "because": "alpha", "to": moved}
    assert core.stop_vm(beta, storage.read_json(beta / "config.json"))["clean_shutdown"]
    first = library.state("alpha")
    assert first["state"] == "running" and core.vmm_active(first)
    assert storage.read_json(alpha / "config.json")["subnet"] == alpha_cfg["subnet"]
    core.stop_vm(alpha, alpha_cfg)
    library.start("beta")  # nothing to move away from now: the note is gone
    assert "subnet_changed" not in library.wait("beta", lambda s: s.get("guest_ready"))


def test_windows_keeps_the_subnet_its_firewall_admits(tmp_path):
    from test_core_boundaries import vm_config
    windows = vm_config(windows=True)
    running = {"alpha": ({**windows, "os": "linux", "name": "alpha"}, {})}
    (tmp_path / "vms/win").mkdir(parents=True)
    with pytest.raises(core.Failure, match=r"is in use by the running virtual machine alpha; shut") as refused:
        core.resolve_subnet(tmp_path, tmp_path / "vms/win", windows, running)
    assert "“alpha” uses the same private network" in text.explain(str(refused.value))
    assert not (tmp_path / "vms/win/config.json").exists()
    windows["network_mode"] = "off"
    assert core.resolve_subnet(tmp_path, tmp_path / "vms/win", windows, running) is None


def test_commitments_count_each_vmm_once_with_its_untouched_ram(tmp_path):
    proc = tmp_path / "proc"

    def process(pid, ppid, argv, **status):
        entry = proc / str(pid)
        entry.mkdir(parents=True)
        (entry / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
        (entry / "stat").write_text(f"{pid} (crosvm) S {ppid} 1 1 0")
        (entry / "status").write_text("".join(f"{k}:\t{v} kB\n" for k, v in status.items()))

    vmm = ["/usr/lib/gunyahvm/crosvm/crosvm", "--log-level", "info", "run", "--hypervisor",
           "gunyah", "--name", "gunyahvm-alpha", "--cpus", "8", "--mem", "6144", "kernel"]
    process(100, 1, ["/usr/bin/python3", "supervisor.py", "run", "store", "alpha"])
    process(101, 100, vmm, VmLck=2048 * 1024, RssShmem=2100 * 1024)
    process(102, 101, vmm, VmLck=0, RssShmem=6000 * 1024)  # sandboxed device fork
    process(200, 1, ["/usr/lib/gunyahvm/crosvm/crosvm", "run", "--hypervisor", "gunyah",
                     "--mem", "9216", "--cpus", "6"], VmLck=9216 * 1024)
    process(300, 1, ["/usr/bin/qemu-system-aarch64", "--mem", "4096"])
    (proc / "self").mkdir()
    commitments = host.guest_commitments(proc)
    assert commitments == [
        {"pid": 101, "name": "alpha", "memory_mib": 6144, "resident_mib": 2100},
        {"pid": 200, "name": "", "memory_mib": 9216, "resident_mib": 9216},
    ]
    memory = {"MemAvailable": 9000}
    assert host.memory_shortage(4096, memory, commitments) is None  # 9000 - 4044 left
    reason = host.memory_shortage(6144, memory, commitments, {200: "mac"})
    assert reason == ("Need 6144 MiB available host memory; 4956 MiB is free for it because "
                      "running virtual machines (alpha, mac) will still take 4044 MiB")
    assert host.memory_shortage(9001, memory, []) == (
        "Need 9001 MiB available host memory; 9000 MiB is free for it")


def test_new_and_cloned_linux_vms_get_their_own_subnet(tmp_path, monkeypatch):
    ident = "00000005-0000-4000-8000-000000000000"
    assert config.free_subnet(ident) == "10.203.6.0/24"
    assert config.free_subnet(ident, ["10.203.6.0/24", "10.203.7.0/24"]) == "10.203.8.0/24"
    assert config.free_subnet("000000f9-0000-4000-8000-000000000000",
                              ["10.203.250.0/24"]) == "10.203.1.0/24"
    with pytest.raises(ValueError, match="--subnet"):
        config.free_subnet(ident, ["10.203.0.0/16"])
    monkeypatch.setattr(core.pwd, "getpwuid", lambda _: SimpleNamespace(pw_dir=str(tmp_path)))
    store = tmp_path / "store"
    vm = store / "vms/test"
    (vm / "boot").mkdir(parents=True, mode=0o700)
    cfg = config.complete_new({
        "id": ident, "uid": os.getuid(), "gid": os.getgid(), "name": "test", "os": "linux",
        "cpus": 2, "memory_mib": 1024, "disk_gib": 8, "subnet": "10.203.6.0/24",
        "vhost_net": False, "username": "ubuntu", "ssh_public_key": "ssh-ed25519 fixture",
        "ssh_identity": str(vm / "agent-key"),
    })
    storage.atomic_json(vm / "config.json", cfg)
    storage.atomic_json(vm / "state.json", {"state": "stopped", "clean_shutdown": True})
    for item in ("disk.raw", "seed.iso", "agent-key", "boot/Image", "boot/initrd.img", "boot/cmdline"):
        (vm / item).write_text("fixture")
    clone = storage.read_json(snapshots.clone(store, "test", "copy") / "config.json")
    assert clone["subnet"] != cfg["subnet"]
    assert ipaddress.ip_network(clone["subnet"]).subnet_of(ipaddress.ip_network("10.203.0.0/16"))
