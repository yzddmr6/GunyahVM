# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Client for the one fixed-action polkit helper."""

import json
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


def start(store, name, display_socket=None):
    # pkexec strips session variables. Pass only the socket name as data; the
    # ordinary-user entry validates it and reconstructs the session environment.
    display = str(display_socket) if display_socket is not None else os.environ.get("WAYLAND_DISPLAY", "")
    if display and not display.startswith("/"):
        display = str(Path(f"/run/user/{os.getuid()}") / display)
    return call("run", store.expanduser().resolve(), name, display)


def firmware_info():
    """Read only the fixed helper's public RM summary, never a caller path."""
    try:
        result = subprocess.run(
            ["pkexec", "--disable-internal-agent", HELPER, "firmware-info"],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            check=True, timeout=10,
        )
        data = json.loads(result.stdout)
        if set(data) != {"hypervisor", "capability_ids"} or type(data["hypervisor"]) is not bool:
            raise ValueError("invalid RM summary fields")
        caps = data["capability_ids"]
        if not isinstance(caps, dict) or any(
            not isinstance(name, str) or not isinstance(ids, list) or len(ids) != 2
            or any(type(value) is not int or not 0 <= value < 2**64 for value in ids)
            for name, ids in caps.items()
        ):
            raise ValueError("invalid RM capability IDs")
        return data
    except (subprocess.SubprocessError, ValueError, TypeError, KeyError) as exc:
        raise OSError("read-only RM helper failed: " + str(exc)) from exc
