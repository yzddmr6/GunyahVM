# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Derived from GunyahVM fb2bbdc715a970a5c4a9e801d1e600012ac42592,
# ordinary-user launcher and restricted NetworkManager helper.
"""Only root actions: load discovered drivers, owned TAP setup/teardown, finite limit + drop."""

import json
import os
from pathlib import Path
import pwd
import re
import resource
import subprocess
import sys
import uuid

from . import host, config

ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"}


def run(argv, check=True, timeout=20):
    return subprocess.run(
        argv, env=ENV, check=check, capture_output=True, text=True, timeout=timeout
    )


def caller():
    value = os.environ.get("PKEXEC_UID", "")
    if os.geteuid() != 0 or not value.isdecimal() or int(value) <= 0:
        raise ValueError("requires an identified ordinary polkit caller")
    if os.readlink("/proc/self/ns/mnt") != os.readlink("/proc/1/ns/mnt") or os.readlink(
        "/proc/self/ns/net"
    ) != os.readlink("/proc/1/ns/net"):
        raise ValueError("requires the host namespaces")
    return pwd.getpwuid(int(value))


def load():
    if Path("/etc/initrd-release").exists():
        raise ValueError("cannot load VM management drivers in initrd")
    host.firmware().require()
    names = host.module_names()
    if names:
        run(["/usr/sbin/modprobe", "--all", *names])
    run(["/usr/bin/udevadm", "settle", "--timeout=10"])
    if not Path("/dev/gunyah").is_char_device():
        raise ValueError("running kernel does not provide /dev/gunyah")


def load_if_absent():
    """The first VM after boot loads the drivers; nothing loads them at boot."""
    if not Path("/dev/gunyah").is_char_device():
        load()


def network_commands(uid, user, ident, subnet):
    identity = config.network_identity(ident, uid)
    network = config.private_subnet(subnet)
    add = [
        "nmcli",
        "connection",
        "add",
        "save",
        "no",
        "type",
        "tun",
        "con-name",
        identity["connection"],
        "ifname",
        identity["tap"],
        "mode",
        "tap",
        "owner",
        str(uid),
        "tun.vnet-hdr",
        "yes",
        "connection.uuid",
        identity["connection_uuid"],
        "connection.permissions",
        "user:" + user,
        "ipv4.method",
        "shared",
        "ipv4.addresses",
        str(network.network_address + 1) + "/24",
        "ipv6.method",
        "disabled",
        "connection.autoconnect",
        "no",
    ]
    return identity, add


def network(action, account, ident, subnet, acceleration):
    if str(uuid.UUID(ident)) != ident or acceleration not in ("vhost", "userspace"):
        raise ValueError("invalid network arguments")
    identity, add = network_commands(account.pw_uid, account.pw_name, ident, subnet)
    connection, tap, name = (
        identity["connection_uuid"],
        identity["tap"],
        identity["connection"],
    )
    query = run(
        [
            "nmcli",
            "-g",
            "connection.id,connection.interface-name,tun.owner",
            "connection",
            "show",
            "uuid",
            connection,
        ],
        check=False,
        timeout=5,
    )
    if query.returncode == 0:
        if query.stdout.strip().splitlines() != [name, tap, str(account.pw_uid)]:
            raise ValueError("existing NM connection does not belong to this caller/VM")
        if action == "net-up":
            raise ValueError(
                "owned network already exists; stop/clean the previous run first"
            )
        run(
            ["nmcli", "--wait", "15", "connection", "down", "uuid", connection],
            check=False,
            timeout=17,
        )
        run(["nmcli", "connection", "delete", "uuid", connection], timeout=5)
        if Path("/sys/class/net", tap).exists():
            run(["nmcli", "device", "delete", tap], timeout=5)
    elif query.returncode != 10:
        raise ValueError("cannot query NetworkManager: " + query.stderr.strip())
    elif Path("/sys/class/net", tap).exists():
        raise ValueError("TAP exists without an owned NM profile")
    if action == "net-down":
        return
    import ipaddress

    for route in json.loads(
        run(["ip", "-j", "-4", "route", "show", "table", "all"]).stdout
    ):
        dst = route.get("dst", "default")
        if dst != "default" and ipaddress.ip_network(dst, strict=False).overlaps(
            config.private_subnet(subnet)
        ):
            raise ValueError("VM subnet overlaps host route " + dst)
    if acceleration == "vhost" and not Path("/dev/vhost-net").is_char_device():
        run(["/usr/sbin/modprobe", "vhost_net"])
        run(["/usr/bin/udevadm", "settle", "--timeout=10"])
    run(add)
    try:
        run(
            ["nmcli", "--wait", "30", "connection", "up", "uuid", connection],
            timeout=35,
        )
    except BaseException:
        run(["nmcli", "connection", "delete", "uuid", connection], check=False)
        if Path("/sys/class/net", tap).exists():
            run(["nmcli", "device", "delete", tap], check=False)
        raise


def prepare_shared_memory():
    """Let a shared-memory guest's boot window get 2 MiB host blocks.

    Gunyah refuses a guest whose RAM is mostly 4 KiB-backed (RM error 0x9),
    because that needs more stage-2 mappings than it can build. After a macOS
    creation or other VMs, clean page cache sits between the free fragments and
    compaction alone does not merge them; dropping clean page cache first lets
    compaction and the VMM's collapse find whole 2 MiB blocks again. The window
    is the guest's whole RAM for every pseudo-unprotected guest: macOS, Windows
    with the GPU and Linux in shared-memory mode.
    """
    for knob in ("drop_caches", "compact_memory"):
        try:
            Path("/proc/sys/vm", knob).write_text("1")
        except OSError:
            pass


def launch(account, store, name, wayland, guest):
    if (
        not os.path.isabs(store)
        or any(c in store for c in "\0\n,:")
        or not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", name)
    ):
        raise ValueError("requires an absolute store and valid VM name")
    # Root never opens user config, disk images, shares or session sockets.
    if wayland and (
        not wayland.startswith(f"/run/user/{account.pw_uid}/")
        or ".." in Path(wayland).parts
        or any(c in wayland for c in "\n\0,:")
    ):
        raise ValueError("Wayland socket must be within the caller runtime directory")
    if guest not in ("shared", "other"):
        raise ValueError("unknown guest kind")
    load_if_absent()
    fw = host.firmware().require()
    if guest == "shared":
        prepare_shared_memory()
    limit = host.memory_budget()["memlock_mib"] * host.MIB
    resource.setrlimit(resource.RLIMIT_MEMLOCK, (limit, limit))
    env = {
        **ENV,
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "XDG_RUNTIME_DIR": f"/run/user/{account.pw_uid}",
        "WAYLAND_DISPLAY": wayland,
        "DBUS_SESSION_BUS_ADDRESS": f"unix:path=/run/user/{account.pw_uid}/bus",
        "GUNYAHVM_HOST_INFO": json.dumps(fw.summary()),
    }
    os.chdir("/")
    os.umask(0o077)
    os.initgroups(account.pw_name, account.pw_gid)
    os.setresgid(account.pw_gid, account.pw_gid, account.pw_gid)
    os.setresuid(account.pw_uid, account.pw_uid, account.pw_uid)
    os.execve(
        "/usr/bin/python3",
        [
            "python3",
            "-I",
            "/usr/lib/gunyahvm/python/gunyahvm/supervisor.py",
            "start",
            store,
            name,
        ],
        env,
    )


def assemble(account, work):
    """Run the macOS assembly helper VM as the caller, like an ordinary VM.

    Root reads nothing in WORK: it raises the locked-memory limit and drops
    privileges; the caller's own process validates and boots the helper.
    """
    home = os.path.realpath(account.pw_dir)
    if (
        not os.path.isabs(work)
        or any(c in work for c in "\0\n,:")
        or os.path.realpath(work) != work
        or not work.startswith(home + "/")
        or os.path.basename(os.path.dirname(work)) != ".creating"
    ):
        raise ValueError("requires a macOS creation directory in the caller's VM library")
    load_if_absent()
    fw = host.firmware().require()
    limit = host.memory_budget()["memlock_mib"] * host.MIB
    if limit < 2048 * host.MIB:
        raise ValueError("insufficient host RAM for the macOS assembly helper")
    resource.setrlimit(resource.RLIMIT_MEMLOCK, (limit, limit))
    env = {
        **ENV,
        "HOME": account.pw_dir,
        "USER": account.pw_name,
        "LOGNAME": account.pw_name,
        "XDG_RUNTIME_DIR": f"/run/user/{account.pw_uid}",
        "GUNYAHVM_HOST_INFO": json.dumps(fw.summary()),
    }
    os.chdir("/")
    os.umask(0o077)
    os.initgroups(account.pw_name, account.pw_gid)
    os.setresgid(account.pw_gid, account.pw_gid, account.pw_gid)
    os.setresuid(account.pw_uid, account.pw_uid, account.pw_uid)
    os.execve(
        "/usr/bin/python3",
        ["python3", "-I", "/usr/lib/gunyahvm/python/gunyahvm/macos_helper.py", work],
        env,
    )


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("load", "net-up", "net-down", "run", "firmware-info",
                                   "assemble"):
        raise ValueError("unknown fixed action")
    action = argv[0]
    expected = {"load": 1, "firmware-info": 1, "assemble": 2, "run": 5}.get(action, 4)
    if len(argv) != expected:
        raise ValueError("invalid fixed action arguments")
    account = caller()
    if action == "firmware-info":
        firmware = host.firmware_from_nodes(host.parse_fdt(host.FDT.read_bytes()), "live FDT")
        print(json.dumps({"hypervisor": firmware.hypervisor,
                          "capability_ids": firmware.capability_ids}))
        return
    if action == "load":
        return load()
    if action == "run":
        return launch(account, *argv[1:])
    if action == "assemble":
        return assemble(account, argv[1])
    return network(action, account, *argv[1:])
