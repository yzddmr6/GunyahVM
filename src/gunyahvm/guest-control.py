#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Readiness and fixed hvc0 commands adapted from iceland-vms (mzakamin-hub)
# d628b9b25d08e074b34a2f1a6113ab23767b78d7 guest-tools/gvm-agent.
import json
import os
import subprocess
import tty
from pathlib import Path


def setup_report(run=subprocess.run):
    """cloud-init's final result, reduced to what the host shows.

    A first boot whose setup failed (for example a package that could not be
    installed) still has a working system, network and shutdown channel, so the
    guest reports ready and carries the setup status instead of staying silent.
    """
    run(["cloud-init", "status", "--wait"], stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL, check=False)
    result = run(["cloud-init", "status", "--format", "json"], capture_output=True,
                 text=True, check=False)
    try:
        report = json.loads(result.stdout)
    except ValueError:
        report = {}
    if not isinstance(report, dict):
        report = {}
    status = str(report.get("extended_status") or report.get("status")
                 or ("done" if result.returncode == 0 else "error"))
    problems = [str(item) for item in report.get("errors") or []]
    recoverable = report.get("recoverable_errors") or {}
    if isinstance(recoverable, dict):
        for level, messages in sorted(recoverable.items()):
            problems += [f"{level}: {message}" for message in messages or []]
    return {"status": status, "problems": [p[:400] for p in problems[:8]]}


def main():
    setup = setup_report()
    fd = os.open("/dev/hvc0", os.O_RDWR | os.O_NOCTTY)
    tty.setraw(fd)
    try:
        subprocess.run(
            ["systemctl", "is-active", "--quiet", "gunyahvm-guest-setup.service"],
            check=True,
        )
    except Exception as exc:
        os.write(fd, ("GUNYAHVM_ERROR " + str(exc) + "\n").encode())
        raise
    addresses = json.loads(
        subprocess.check_output(["ip", "-j", "-4", "addr", "show"], text=True)
    )
    ips = [
        a["local"]
        for i in addresses
        for a in i.get("addr_info", [])
        if a.get("scope") == "global"
    ]
    ready = {
        "ipv4": ips,
        "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
        "setup": setup,
    }
    os.write(fd, ("\nGUNYAHVM_READY " + json.dumps(ready) + "\n").encode())
    buf = b""
    while True:
        data = os.read(fd, 1024)
        if not data:
            break
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if line.strip() == b"poweroff":
                os.write(fd, b"GUNYAHVM_POWEROFF_REQUEST\n")
                subprocess.run(["systemctl", "poweroff", "--no-block"], check=True)
            else:
                os.write(fd, b"GUNYAHVM_UNKNOWN_COMMAND\n")
        if len(buf) > 4096:
            buf = b""


if __name__ == "__main__":
    main()
