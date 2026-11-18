# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""A guest controlled over the network is known ready as soon as it can answer."""

import os
import socket
import struct
import sys
import threading
import time
from types import SimpleNamespace

import pytest

from gunyahvm import core, storage, windows_agent
from test_core_boundaries import vm_config


class Events:
    """Neighbour events of a guest that gets its address at AT seconds."""

    def __init__(self, clock, at):
        self.clock, self.at, self.sent = clock, at, False

    def pending(self):
        if not self.sent and self.clock() >= self.at:
            self.sent = True
            return True
        return False

    def close(self):
        pass


def timeline(readiness, now, address_at, listening_at, end=120.0):
    """Drive READINESS as the supervisor loop does (every 0.2 s); return when the guest
    was found ready and the times it was asked."""
    asked = []
    while now[0] < end:
        if readiness.wanted(now[0]):
            asked.append(now[0])
            address = now[0] >= address_at
            ready = address and now[0] >= listening_at
            readiness.done(now[0], address or None, ready)
            if ready:
                return now[0], asked
        now[0] = round(now[0] + 0.2, 1)
    return None, asked


@pytest.mark.parametrize("listening_at", [54.3, 57.9, 63.9])
def test_ready_follows_the_address_event_and_the_open_port(listening_at):
    # Measured on a 1.4.0 macOS guest: address at 54 s, ready reported at 64.5 s.
    now = [0.0]
    readiness = windows_agent.Readiness(Events(lambda: now[0], 54.2))
    ready, asked = timeline(readiness, now, 54.2, listening_at)
    assert ready - listening_at <= windows_agent.Readiness.LISTENING + 0.2
    assert asked[:6] == [0.0, 10.0, 20.0, 30.0, 40.0, 50.0]  # no address: as before
    assert asked[6] == 54.2  # the event, not the next ten-second probe


def test_without_events_the_address_is_found_by_the_ten_second_probe():
    now = [0.0]
    ready, asked = timeline(windows_agent.Readiness(None), now, 54.2, 57.9)
    assert asked[6] == 60.0 and ready == 60.0


def test_neighbour_events_are_those_of_this_tap():
    events = windows_agent.NeighbourEvents.__new__(windows_agent.NeighbourEvents)
    events.index = 7

    def message(kind, index):
        body = struct.pack("=BBHiHBB", socket.AF_INET, 0, 0, index, 2, 0, 1) + b"\0" * 8
        return struct.pack("=IHHII", 16 + len(body), kind, 0, 0, 0) + body

    assert events.matches(message(windows_agent.RTM_NEWNEIGH, 7))
    assert not events.matches(message(windows_agent.RTM_NEWNEIGH, 8))
    assert not events.matches(message(29, 7))  # RTM_DELNEIGH
    assert events.matches(message(29, 7) + message(windows_agent.RTM_NEWNEIGH, 3)
                          + message(windows_agent.RTM_NEWNEIGH, 7))
    assert not events.matches(b"\x05\0\0\0")


def test_an_ordinary_user_can_listen_for_neighbour_events():
    events = windows_agent.neighbour_events("lo")
    if events is None:
        pytest.skip("no rtnetlink in this environment")
    try:
        assert events.index == socket.if_nametoindex("lo") and events.pending() is False
    finally:
        events.close()
    assert windows_agent.neighbour_events("gvm-absent") is None


def test_supervisor_finds_a_guest_ready_once_its_control_port_opens(tmp_path, monkeypatch):
    """The real supervisor loop with a Windows guest whose address is known at once."""
    cfg = vm_config(windows=True)
    storage.atomic_json(tmp_path / "state.json", {"state": "created"})
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    marker = tmp_path / "powered-off"
    vmm = (f"import time; from pathlib import Path\n"
           f"while not Path({str(marker)!r}).exists(): time.sleep(0.05)\n")
    monkeypatch.setattr(core, "access_limits", lambda cfg, check_vhost=True: (1 << 40, 1 << 40))
    monkeypatch.setattr(core, "host_snapshot", lambda: SimpleNamespace(
        require=lambda cfg, names=None: None, profile={}, runtime={}))
    monkeypatch.setattr(core.capabilities, "sensors", lambda *a: {"batteries": [], "temperatures": []})
    monkeypatch.setattr(core, "network_action", lambda *a: None)
    monkeypatch.setattr(core.signal, "signal", lambda *a: None)
    monkeypatch.setattr(core, "SleepGuard", lambda stop: SimpleNamespace(close=lambda: None))
    monkeypatch.setattr(core.crosvmargs, "build", lambda *a: ([sys.executable, "-c", vmm], {}))
    monkeypatch.setattr(windows_agent, "neighbour_events", lambda tap: None)
    monkeypatch.setattr(windows_agent, "guest_ip", lambda cfg: "127.0.0.1")
    monkeypatch.setattr(windows_agent, "control_port", lambda cfg: port)

    def ssh(vm, cfg, ip, command):  # succeeds only while the guest listens, as SSH does
        up = windows_agent.port_open(ip, port)
        return SimpleNamespace(returncode=0 if up else 255, stdout="GUNYAHVM_READY\n" if up else "")

    monkeypatch.setattr(windows_agent, "command", ssh)
    monkeypatch.setattr(windows_agent, "poweroff", lambda vm, cfg, ip: (marker.touch(), True)[1])
    supervisor = threading.Thread(target=core.supervise, args=(tmp_path, cfg))
    supervisor.start()
    state = lambda: storage.read_json(tmp_path / "state.json")
    try:
        deadline = time.monotonic() + 20
        while state().get("state") != "running" and time.monotonic() < deadline:
            time.sleep(0.02)
        time.sleep(2.5)  # the guest is up, its SSH server is not yet
        assert not state().get("guest_ready")
        listener = socket.create_server(("127.0.0.1", port))
        opened = time.monotonic()
        while not state().get("guest_ready") and time.monotonic() < opened + 15:
            time.sleep(0.02)
        assert time.monotonic() - opened <= windows_agent.Readiness.LISTENING + 0.6
        (tmp_path / "stop.requested").touch()
        supervisor.join(20)
        assert state()["state"] == "stopped" and state()["clean_shutdown"]
    finally:
        marker.touch()
        supervisor.join(20)
        if "listener" in locals():
            listener.close()
