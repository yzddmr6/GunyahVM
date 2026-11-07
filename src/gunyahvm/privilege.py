# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Client for the one fixed-action polkit helper."""

import os
from pathlib import Path
import subprocess

HELPER = "/usr/libexec/gunyahvm/gunyahvm-helper"


def call(action, *args):
    return subprocess.run(
        ["pkexec", HELPER, action, *map(str, args)],
        check=True,
        timeout=40 if action == "net-down" else 180 if action == "run" else 90,
    )


def network_action(action, cfg):
    if action == "up" and cfg["network_mode"] == "off":
        return
    return call(
        "net-up" if action == "up" else "net-down",
        cfg["id"],
        cfg["subnet"],
        "vhost" if cfg["vhost_net"] else "userspace",
    )


def start(store, name):
    # pkexec strips session variables. Pass only the socket name as data; the
    # ordinary-user entry validates it and reconstructs the session environment.
    display = os.environ.get("WAYLAND_DISPLAY", "")
    if display and not display.startswith("/"):
        display = str(Path(f"/run/user/{os.getuid()}") / display)
    return call("run", store.expanduser().resolve(), name, display)
