# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Admission guards shared by bounded device validation runners.

Retains the original validation runner's session and watchdog corrections:
systemd user managers are not interactive logins; some watchdog drivers do
not expose an active sysfs state even while PID 1 holds the watchdog open.
"""

import json
import os
from pathlib import Path
import re
import subprocess


def permitted_session(properties, test_user, ancestors, desktop_session=None):
    """Allow greeters, the driver's SSH ancestry and known user managers.

    A test desktop must be explicitly identified; a matching username alone
    must never exempt an interactive owner login.
    """
    p = properties
    if p.get("Class") == "greeter":
        return True
    if (
        p.get("Class") in ("manager", "manager-early")
        and p.get("Type") == "unspecified"
        and not p.get("Seat")
        and p.get("Name") in ("gdm", "Debian-gdm", "gdm-greeter", test_user)
    ):
        return True
    if p.get("Remote") == "yes" and p.get("Leader") in ancestors:
        return True
    return bool(
        desktop_session
        and p.get("Id") == desktop_session
        and p.get("Name") == test_user
        and p.get("Class") == "user"
        and p.get("Type") == "wayland"
        and p.get("Remote") == "no"
    )


def watchdog_armed(root=Path("/"), check_output=subprocess.check_output):
    configured = check_output(
        ["systemctl", "show", "--value", "-p", "RuntimeWatchdogUSec"], text=True
    ).strip()
    if configured in ("", "0", "0s", "infinity"):
        return False
    if any(
        p.read_text().strip() == "active"
        for p in (root / "sys/class/watchdog").glob("watchdog*/state")
    ):
        return True
    opened = False
    for p in (root / "proc/1/fd").iterdir():
        try:
            opened |= bool(re.fullmatch(r"/dev/watchdog\d*", os.readlink(p)))
        except FileNotFoundError:
            continue
    if not opened:
        return False
    # PID 1 may log through /dev/kmsg during daemon-reexec. Those records
    # have trusted kernel transport plus SYSLOG_PID=1, but no _PID field.
    journal = check_output(
        ["journalctl", "-b", "--no-pager", "-o", "json", "_PID=1", "+",
         "_TRANSPORT=kernel", "SYSLOG_IDENTIFIER=systemd", "SYSLOG_PID=1"],
        text=True,
    )
    for line in journal.splitlines():
        entry = json.loads(line)
        trusted = entry.get("_PID") == "1" or (
            entry.get("_TRANSPORT") == "kernel"
            and entry.get("SYSLOG_IDENTIFIER") == "systemd"
            and entry.get("SYSLOG_PID") == "1"
        )
        message = entry.get("MESSAGE", "")
        if trusted and isinstance(message, str) and re.fullmatch(
            r"Watchdog running with a hardware timeout of (?!0s\.)[^.]+\.", message
        ):
            return True
    return False


def host_battery_levels(root=Path("/")):
    """Read host batteries; peripheral batteries do not describe host charge."""
    levels = []
    for supply in (root / "sys/class/power_supply").glob("*"):
        kind = supply / "type"
        if not kind.exists() or kind.read_text().strip() != "Battery":
            continue
        scope = supply / "scope"
        if scope.exists() and scope.read_text().strip() == "Device":
            continue
        # Missing/invalid host capacity remains an admission error.
        levels.append(int((supply / "capacity").read_text()))
    return levels
