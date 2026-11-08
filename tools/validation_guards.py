# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Admission guards shared by bounded device validation runners.

Retains the original validation runner's session and watchdog corrections:
systemd user managers are not interactive logins; some watchdog drivers do
not expose an active sysfs state even while PID 1 holds the watchdog open.
"""

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
    journal = check_output(["journalctl", "-b", "--no-pager", "_PID=1"], text=True)
    return bool(re.search(r"Watchdog running with a hardware timeout of (?!0s)[^.]+\.", journal))
