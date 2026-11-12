#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Fixed entry executed after dropping root; VMs outlive frontend windows."""

import os
from pathlib import Path
import sys
import time
import uuid

sys.dont_write_bytecode = True
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gunyahvm import core


def independent_scope(ident):
    """Move before forking the VMM; setsid alone does not escape a GUI cgroup.

    A scope preserves the already granted memlock limit and inherited host
    lock, while letting the user manager own the VM's complete process tree.
    """
    import dbus
    unit = "gunyahvm-" + str(uuid.UUID(ident)) + "-" + str(os.getpid()) + ".scope"
    bus = dbus.SessionBus()
    manager = dbus.Interface(bus.get_object("org.freedesktop.systemd1", "/org/freedesktop/systemd1"),
                             "org.freedesktop.systemd1.Manager")
    manager.StartTransientUnit(unit, "fail", dbus.Array([
        ("Description", dbus.String("GunyahVM virtual machine")),
        ("Slice", dbus.String("app.slice")),
        ("CollectMode", dbus.String("inactive-or-failed")),
        ("PIDs", dbus.Array([dbus.UInt32(os.getpid())], signature="u")),
    ], signature="(sv)"), dbus.Array([], signature="(sa(sv))"))
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if any(line.endswith("/" + unit) for line in Path("/proc/self/cgroup").read_text().splitlines()):
            return
        time.sleep(0.02)
    raise RuntimeError("The user manager did not isolate the VM from its frontend")


def main():
    if os.geteuid() == 0:
        raise ValueError("supervisor must run as an ordinary user")
    action, store, name, *extra = sys.argv[1:]
    store = Path(store)
    core.vm_name(name)
    if action == "start" and not extra:
        return core.start_vm_authorized(store, name)
    if action == "run" and len(extra) == 1:
        vm, cfg = core.owned_vm(store, name, os.getuid())
        independent_scope(cfg["id"])
        return core.supervise(vm, cfg, int(extra[0]))
    raise ValueError("invalid supervisor arguments")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
