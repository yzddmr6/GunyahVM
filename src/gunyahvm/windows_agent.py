# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7, win_ssh_argv/WinAgent.
"""Key-only Windows control; an accepted request is distinct from a stopped VM."""

import base64
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


def command(vm, cfg, ip, cmd, timeout=15):
    return subprocess.run(
        win_ssh_argv(
            [vm / "agent-key"], cfg["windows"]["ssh_user"], ip, vm / "known_hosts", cmd
        ),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


STANDBY_OFF = (
    "powercfg /change standby-timeout-ac 0 && powercfg /change standby-timeout-dc 0"
    " && echo GUNYAHVM_STANDBY_OFF"
)


def turn_off_standby(vm, cfg, ip):
    """Keep Windows out of Modern Standby, as setup does for new VMs.

    Idle for five minutes, Windows 11 sleeps and drops the network, which cuts
    this control channel until someone touches the VM.
    """
    result = command(vm, cfg, ip, STANDBY_OFF)
    return result.returncode == 0 and "GUNYAHVM_STANDBY_OFF" in result.stdout


# Windows shows its setup pages (CloudExperienceHost) after sign-in: the privacy
# settings page at an account's first sign-in, and now and then "finish setting up
# this device" and the welcome experience after updates. Drawn with the GPU driver
# they hang the host GPU (GitHub issue #1). The privacy page is turned off for the
# whole VM by the policy "Don't launch privacy settings experience on user logon";
# the other two by the switches under Settings > System > Notifications > Additional
# settings, set off for every account and for the default profile new accounts
# start from.
SETUP_PAGES_OFF = r"""$ErrorActionPreference = 'Stop'
reg.exe add "HKLM\SOFTWARE\Policies\Microsoft\Windows\OOBE" /v DisablePrivacyExperience /t REG_DWORD /d 1 /f | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Could not turn off the privacy settings page' }
function Off($root) {
  reg.exe add "$root\Software\Microsoft\Windows\CurrentVersion\UserProfileEngagement" /v ScoobeSystemSettingEnabled /t REG_DWORD /d 0 /f | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not change $root" }
  reg.exe add "$root\Software\Microsoft\Windows\CurrentVersion\ContentDeliveryManager" /v SubscribedContent-310093Enabled /t REG_DWORD /d 0 /f | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not change $root" }
}
$profiles = @(Get-CimInstance Win32_UserProfile | Where-Object { -not $_.Special } |
  ForEach-Object { @{ Sid = $_.SID; Hive = Join-Path $_.LocalPath 'NTUSER.DAT' } })
$profiles += @{ Sid = $null; Hive = Join-Path $env:SystemDrive 'Users\Default\NTUSER.DAT' }
foreach ($p in $profiles) {
  if ($p.Sid -and (Test-Path -LiteralPath "Registry::HKEY_USERS\$($p.Sid)")) { Off "HKU\$($p.Sid)"; continue }
  if (-not (Test-Path -LiteralPath $p.Hive)) { continue }
  reg.exe load HKU\GunyahVMSetupPages $p.Hive | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "Could not load $($p.Hive)" }
  try { Off 'HKU\GunyahVMSetupPages' } finally { reg.exe unload HKU\GunyahVMSetupPages | Out-Null }
}
'GUNYAHVM_SETUP_PAGES_OFF'
"""


def turn_off_setup_pages(vm, cfg, ip):
    """Keep Windows from showing its setup pages again after sign-in (SETUP_PAGES_OFF).

    PowerShell's first start in a fresh Windows can take longer than a plain
    command, and a failure here must not hold up the steps after it. The
    supervisor waits for the answer (stop requests and the thermal guard with
    it), so the wait stays bounded and the caller tries once per start.
    """
    script = base64.b64encode(SETUP_PAGES_OFF.encode("utf-16le")).decode("ascii")
    try:
        result = command(vm, cfg, ip, "powershell -NoProfile -NonInteractive -EncodedCommand " + script,
                         timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0 and "GUNYAHVM_SETUP_PAGES_OFF" in result.stdout


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
