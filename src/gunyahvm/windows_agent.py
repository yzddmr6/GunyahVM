# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7, win_ssh_argv/WinAgent.
"""Key-only Windows control; an accepted request is distinct from a stopped VM."""

import ipaddress
import json
import socket
import struct
import subprocess

RTMGRP_NEIGH = 4
RTM_NEWNEIGH = 28
_NLMSGHDR = struct.Struct("=IHHII")  # len, type, flags, seq, pid
_NDMSG = struct.Struct("=BBHiHBB")  # family, pad, pad, ifindex, state, flags, type


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


def control_port(cfg):
    """The guest's SSH control port: macOS has its own, Windows OpenSSH listens on 22."""
    return cfg["macos"]["agent_port"] if cfg["os"] == "macos" else 22


def port_open(ip, port, timeout=0.5):
    """Whether the guest accepts a TCP connection on PORT now; costs the guest nothing."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


class NeighbourEvents:
    """Wakes the supervisor when the host learns a guest address on the VM's TAP.

    The host's DHCP server answers a guest by adding it to the TAP's
    neighbour table, and rtnetlink announces that to any listener; no
    privilege is needed to listen.
    """

    def __init__(self, tap):
        self.index = socket.if_nametoindex(tap)
        self.sock = socket.socket(socket.AF_NETLINK,
                                  socket.SOCK_RAW | socket.SOCK_NONBLOCK | socket.SOCK_CLOEXEC,
                                  socket.NETLINK_ROUTE)
        try:
            self.sock.bind((0, RTMGRP_NEIGH))
        except OSError:
            self.sock.close()
            raise

    def matches(self, data):
        while len(data) >= _NLMSGHDR.size:
            length, kind = _NLMSGHDR.unpack_from(data)[:2]
            if length < _NLMSGHDR.size or length > len(data):
                return False
            if kind == RTM_NEWNEIGH and length >= _NLMSGHDR.size + _NDMSG.size:
                if _NDMSG.unpack_from(data, _NLMSGHDR.size)[3] == self.index:
                    return True
            data = data[(length + 3) & ~3:]
        return False

    def pending(self):
        """Whether the TAP gained or changed a neighbour since the last call."""
        found = False
        while True:
            try:
                data = self.sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                return found
            except OSError:
                return True  # overrun: events were lost, so look now
            found = self.matches(data) or found

    def close(self):
        self.sock.close()


def neighbour_events(tap):
    try:
        return NeighbourEvents(tap)
    except OSError:
        return None


class Readiness:
    """When the supervisor asks a guest controlled over the network whether it is ready.

    The guest can answer only once it has an address and its SSH server
    listens. The address arrives with an event (NeighbourEvents) rather than
    with the next probe. From then until the control port accepts a TCP
    connection, the port is tried every LISTENING seconds; SSH runs once it
    does. A guest without an address, or one that is ready, is asked every
    IDLE seconds, as before.
    """

    IDLE = 10
    LISTENING = 1

    def __init__(self, events=None):
        self.events = events
        self.due = 0.0

    def wanted(self, now):
        woke = self.events is not None and self.events.pending()
        return woke or now >= self.due

    def done(self, now, address, ready):
        self.due = now + (self.LISTENING if address and not ready else self.IDLE)

    def close(self):
        if self.events is not None:
            self.events.close()


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
