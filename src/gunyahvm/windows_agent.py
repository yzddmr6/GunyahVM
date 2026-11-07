# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7, win_ssh_argv/WinAgent.
"""Key-only Windows control; an accepted request is distinct from a stopped VM."""

import ipaddress
import json
import subprocess


def win_ssh_argv(identities, user, ip, known_hosts, cmd):
    ipaddress.ip_address(ip)
    argv = [
        "ssh",
        "-F",
        "/dev/null",
        "-o",
        "BatchMode=yes",
        "-o",
        "PasswordAuthentication=no",
        "-o",
        "ConnectTimeout=5",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        "UserKnownHostsFile=" + str(known_hosts),
        "-o",
        "LogLevel=ERROR",
        "-o",
        "IdentitiesOnly=yes",
    ]
    for key in identities:
        argv += ["-i", str(key)]
    return argv + [user + "@" + ip, cmd]


def guest_ip(cfg):
    result = subprocess.run(
        ["ip", "-j", "-4", "neigh", "show", "dev", cfg["tap"]],
        capture_output=True,
        text=True,
        timeout=5,
        check=True,
    )
    net = ipaddress.ip_network(cfg["subnet"])
    for row in json.loads(result.stdout):
        if row.get("lladdr", "").lower() == cfg["mac"]:
            ip = ipaddress.ip_address(row["dst"])
            if ip in net and ip not in (
                net.network_address,
                net.network_address + 1,
                net.broadcast_address,
            ):
                return str(ip)
    return None


def command(vm, cfg, ip, cmd):
    return subprocess.run(
        win_ssh_argv(
            [vm / "agent-key"], cfg["windows"]["ssh_user"], ip, vm / "known_hosts", cmd
        ),
        capture_output=True,
        text=True,
        timeout=15,
    )


def poweroff(vm, cfg, ip):
    if not ip:
        raise ValueError("Windows has no guest address; clean shutdown unavailable")
    result = command(vm, cfg, ip, "shutdown.exe /s /t 0")
    if result.returncode:
        raise ValueError("Windows refused clean shutdown: " + result.stderr.strip())
    return True


def rdp_argv(ip, program="remmina"):
    ipaddress.ip_address(ip)
    return (
        [program, "-c", "rdp://" + ip]
        if program == "remmina"
        else [program, "/v:" + ip, "/dynamic-resolution", "/cert:tofu"]
    )
