# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
import json

from validation_guards import permitted_session, watchdog_armed


def test_user_manager_is_not_an_owner_login():
    manager = dict(Name="test", Class="manager", Type="unspecified", Seat="")
    assert permitted_session(manager, "test", set())
    assert not permitted_session(manager | {"Name": "owner"}, "test", set())
    assert not permitted_session(manager | {"Type": "wayland"}, "test", set())
    desktop = dict(Id="7", Name="test", Class="user", Type="wayland", Remote="no")
    assert not permitted_session(desktop, "test", set())
    assert permitted_session(desktop, "test", set(), "7")
    assert not permitted_session(desktop | {"Id": "8"}, "test", set(), "7")


def test_watchdog_needs_current_open_fd_and_this_boot_confirmation(tmp_path):
    fds = tmp_path / "proc/1/fd"
    fds.mkdir(parents=True)
    calls = []

    def output(argv, **kwargs):
        calls.append(argv)
        return "30s\n" if argv[0] == "systemctl" else json.dumps({"_PID": "1", "MESSAGE": "Watchdog running with a hardware timeout of 30s."})

    assert not watchdog_armed(tmp_path, output)
    (fds / "4").symlink_to("/dev/watchdog0")
    assert watchdog_armed(tmp_path, output)
    assert calls[-1] == ["journalctl", "-b", "--no-pager", "-o", "json", "_PID=1", "+", "_TRANSPORT=kernel", "SYSLOG_IDENTIFIER=systemd", "SYSLOG_PID=1"]
    assert not watchdog_armed(tmp_path, lambda *a, **k: "0")
    assert not watchdog_armed(tmp_path, lambda argv, **k: "30s" if argv[0] == "systemctl" else "")


def test_watchdog_reexec_kernel_transport_and_spoofed_syslog(tmp_path):
    fds = tmp_path / "proc/1/fd"
    fds.mkdir(parents=True)
    (fds / "13").symlink_to("/dev/watchdog0")
    record = {"_TRANSPORT": "kernel", "SYSLOG_IDENTIFIER": "systemd",
              "SYSLOG_PID": "1", "MESSAGE": "Watchdog running with a hardware timeout of 20s."}

    def output(argv, **kwargs):
        return "20s" if argv[0] == "systemctl" else json.dumps(record)

    assert watchdog_armed(tmp_path, output)
    record["_TRANSPORT"] = "syslog"
    assert not watchdog_armed(tmp_path, output)
    record["_TRANSPORT"] = "kernel"
    record["SYSLOG_PID"] = "2"
    assert not watchdog_armed(tmp_path, output)
    record["SYSLOG_PID"] = "1"
    record["MESSAGE"] = "Failed to set watchdog hardware timeout to 1min: Invalid argument"
    assert not watchdog_armed(tmp_path, output)
