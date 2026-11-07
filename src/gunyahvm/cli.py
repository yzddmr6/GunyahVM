#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Command-line frontend for the same API used by the GTK application."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from . import core, host, capabilities, config, windows_vm, windows_agent
from .errors import Failure


def parser():
    p = argparse.ArgumentParser(
        description="GunyahVM: ordinary-user Linux and Windows virtual machines"
    )
    p.add_argument("--store", type=Path, default=Path.home() / ".local/share/gunyahvm")
    subs = p.add_subparsers(dest="action", required=True)
    c = subs.add_parser("create")
    c.add_argument("name", type=core.vm_name)
    c.add_argument("--os", choices=("linux", "windows11"), default="linux")
    c.add_argument("--image")
    c.add_argument("--release", default="20261016")
    c.add_argument("--mirror", choices=("official", "tuna", "ustc"), default="official")
    c.add_argument("--boot-dir")
    c.add_argument(
        "--guest-payload",
        action="store_true",
        help="use the runtime package guest kernel/initrd",
    )
    c.add_argument("--ssh-key")
    c.add_argument("--username", type=core.guest_username, default="ubuntu")
    c.add_argument("--disk-gib", type=int)
    c.add_argument("--memory-mib", type=int)
    c.add_argument("--cpus", type=int)
    c.add_argument("--subnet")
    c.add_argument("--network", choices=("nat", "off"), default="nat")
    c.add_argument("--vhost-net", action="store_true")
    c.add_argument("--gpu", choices=config.GPU_MODES)
    c.add_argument(
        "--memory-mode", choices=("protected", "shared"), default="protected"
    )
    c.add_argument("--input-mode", choices=config.INPUT_MODES, default="tablet")
    c.add_argument("--vnc", action="store_true")
    c.add_argument("--vnc-port", type=int, default=5900)
    c.add_argument("--iso", type=Path)
    c.add_argument("--edition")
    c.add_argument("--local-user")
    c.add_argument(
        "--password-fd",
        type=int,
        help="read a local Windows account password from this inherited fd",
    )
    c.add_argument(
        "--windows-user",
        default="gunyahvm-agent",
        help="dedicated guest SSH control account",
    )
    c.add_argument("--start", action="store_true")
    for action in (
        "start",
        "stop",
        "restart",
        "delete",
        "console",
        "plan",
        "view",
        "rdp",
        "ssh",
    ):
        sub = subs.add_parser(action)
        sub.add_argument("name", type=core.vm_name)
        if action == "stop":
            sub.add_argument("--force", action="store_true")
        if action == "console":
            sub.add_argument("--interactive", action="store_true")
        if action == "ssh":
            sub.add_argument("command", nargs=argparse.REMAINDER)
    subs.add_parser("list")
    check = subs.add_parser("check")
    check.add_argument("--json", action="store_true")
    subs.add_parser("about")
    subs.add_parser("load")
    subs.add_parser("gui")
    return p


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        argv = ["gui"]
    a = parser().parse_args(argv)
    if a.action == "about":
        print(host.about())
        return 0
    if a.action == "check":
        snap = capabilities.probe()
        if a.json:
            print(json.dumps(snap.as_dict(), indent=2, ensure_ascii=False))
        rows = capabilities.diagnostic_rows(snap)
        if not a.json:
            for name, good, detail, fix in rows:
                print(
                    ("UNKNOWN" if good is None else "PASS" if good else "FAIL")
                    + f" {name}: {detail}"
                )
                print("  " + fix)
        return (
            1
            if any(r[1] is False for r in rows)
            else 2
            if any(r[1] is None for r in rows)
            else 0
        )
    if os.geteuid() == 0:
        raise Failure("Run GunyahVM as the ordinary VM owner")
    os.umask(0o077)
    if a.action == "gui":
        from .ui.app import main as gui

        return gui(a.store)
    if a.action == "load":
        from .privilege import call

        call("load")
        return 0
    if a.action == "create":
        core.create(a)
        return 0
    if a.action == "list":
        print(json.dumps(core.list_vms(a.store), indent=2, ensure_ascii=False))
        return 0
    if a.action == "delete":
        core.delete_vm(a.store, a.name)
        return 0
    vm, cfg = core.owned_vm(
        a.store, a.name, os.getuid(), inputs=a.action in ("start", "plan")
    )
    if a.action == "start":
        core.start_vm(a.store, a.name)
    elif a.action == "restart":
        core.restart_vm(a.store, a.name)
    elif a.action == "stop":
        core.stop_vm(vm, cfg, a.force)
    elif a.action == "console":
        core.console(vm, cfg, a.interactive)
    elif a.action == "plan":
        from .crosvmargs import build

        args, env = build(
            vm,
            cfg,
            vm / "runs/PLAN",
            capabilities.probe(),
            os.environ.get("WAYLAND_DISPLAY"),
        )
        print(
            json.dumps({"argv": args, "environment": env, "uid": os.getuid()}, indent=2)
        )
    elif a.action == "view":
        if (
            not core.vmm_active(core.read_json(vm / "state.json"))
            or not cfg["display"]["vnc"]
        ):
            raise Failure("view needs a running VNC-enabled VM")
        windows_vm.view(vm, {**cfg, "vnc_port": cfg["display"]["vnc_port"]})
    elif a.action in ("ssh", "rdp"):
        state = core.status(vm, cfg)
        if state["state"] != "running" or not state.get("ipv4"):
            raise Failure("Guest has not reported an address")
        if a.action == "rdp":
            subprocess.Popen(
                windows_agent.rdp_argv(state["ipv4"]), start_new_session=True
            )
        else:
            identity = (
                vm / "agent-key" if cfg["os"] == "windows11" else cfg["ssh_identity"]
            )
            user = (
                cfg["windows"]["ssh_user"]
                if cfg["os"] == "windows11"
                else cfg["username"]
            )
            command = a.command[1:] if a.command[:1] == ["--"] else a.command
            args = windows_agent.win_ssh_argv(
                [identity], user, state["ipv4"], vm / "known_hosts", ""
            )[:-1]
            os.execvp("ssh", args + command)
    return 0


def entrypoint():
    try:
        sys.exit(main())
    except (
        RuntimeError,
        OSError,
        ValueError,
        KeyError,
        subprocess.SubprocessError,
    ) as exc:
        print("gunyahvm: " + str(exc), file=sys.stderr)
        sys.exit(1)
