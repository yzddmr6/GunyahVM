# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Real subprocess lifecycle with only hardware, privilege and logind substituted."""

import fcntl
import os
import sys
import pytest
from gunyahvm import core, storage
from test_capabilities import capable
from test_core_boundaries import vm_config


@pytest.mark.parametrize("mode", ["clean", "force", "reset"])
def test_stop_reaps_vmm_before_network_and_inhibitor(tmp_path, monkeypatch, mode):
    force = mode == "force"
    cfg = vm_config()
    cfg.update(
        ssh_identity=str(tmp_path / "key"),
        ssh_public_key="ssh-ed25519 fixture",
        boot_managed=False,
    )
    (tmp_path / "key.pub").write_text("ssh-ed25519 fixture")
    storage.atomic_json(tmp_path / "config.json", cfg)
    storage.atomic_json(
        tmp_path / "state.json", {"state": "created", "network_cleanup_needed": True}
    )
    monkeypatch.setattr(core, "access_limits", lambda c: (6 * 1024**3, 6 * 1024**3))
    monkeypatch.setattr(core.capabilities, "probe", capable)
    monkeypatch.setattr(
        core.capabilities, "sensors", lambda p: {"batteries": [], "temperatures": []}
    )
    monkeypatch.setattr(core, "seed", lambda *a: None)
    monkeypatch.setattr(core.signal, "signal", lambda *a: None)
    events = []

    class Guard:
        def __init__(self, stop):
            events.append("inhibit")

        def close(self):
            assert not core.vmm_active(storage.read_json(tmp_path / "state.json"))
            events.append("release")

    monkeypatch.setattr(core, "SleepGuard", Guard)

    def network(action, cfg):
        assert not core.vmm_active(storage.read_json(tmp_path / "state.json"))
        events.append(action)

    monkeypatch.setattr(core, "network_action", network)

    def build(vm, cfg, rd, snapshot, wayland):
        program = (
            """
import json, os, time
from pathlib import Path
rd, vm = Path(@RUN@), Path(@VM@)
if @MODE@ == 'reset' and not (vm / 'reset.once').exists():
    (vm / 'reset.once').touch()
    raise SystemExit(32)
with (rd / 'agent.log').open('a') as log:
    log.write('GUNYAHVM_READY ' + json.dumps({'ipv4':['10.203.5.2'], 'boot_id':'guest'}) + '\\n')
    log.flush()
(vm / @REQUEST@).touch()
fd = os.open(rd / 'agent.in', os.O_RDONLY | os.O_NONBLOCK)
deadline = time.monotonic() + 8
while time.monotonic() < deadline:
    try:
        command = os.read(fd, 1024)
    except BlockingIOError:
        command = b''
    if b'poweroff' in command:
        with (rd / 'agent.log').open('a') as log:
            log.write('GUNYAHVM_POWEROFF_REQUEST\\n')
        raise SystemExit(0)
    time.sleep(.02)
raise SystemExit(7)
""".replace("@MODE@", repr(mode))
            .replace("@RUN@", repr(str(rd)))
            .replace("@VM@", repr(str(vm)))
            .replace(
                "@REQUEST@", repr("force-stop.requested" if force else "stop.requested")
            )
        )
        return [sys.executable, "-c", program], {}

    monkeypatch.setattr(core.crosvmargs, "build", build)
    fd = os.open(tmp_path / "lock", os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    result = core.supervise(tmp_path, cfg, fd)
    state = storage.read_json(tmp_path / "state.json")
    assert state["state"] == ("failed" if force else "stopped"), state
    assert state["clean_shutdown"] is (not force), state
    assert state["forced_stop"] is force
    assert result == (1 if force else 0)
    assert not state["network_cleanup_needed"]
    assert not core.vmm_active(state)
    assert events == ["inhibit", "down", "release"]


def test_boot_and_ticks_both_required_for_process_identity(monkeypatch):
    monkeypatch.setattr(core, "boot_id", lambda: "boot")
    monkeypatch.setattr(core, "process_ticks", lambda pid: None)
    assert not core.monitor_active({"supervisor_pid": 1234, "host_boot_id": "boot"})
    assert not core.vmm_active({"pid": 1234, "host_boot_id": "boot"})
    monkeypatch.setattr(core, "process_ticks", lambda pid: "12")
    state = {"pid": 1234, "host_boot_id": "boot", "vmm_ticks": "12"}
    assert core.vmm_active(state)
    state["host_boot_id"] = "previous-boot"
    assert not core.vmm_active(state)
