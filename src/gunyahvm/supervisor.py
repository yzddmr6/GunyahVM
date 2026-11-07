#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Fixed entry executed after dropping root; VMs outlive frontend windows."""

import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gunyahvm import core


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
        return core.supervise(vm, cfg, int(extra[0]))
    raise ValueError("invalid supervisor arguments")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
