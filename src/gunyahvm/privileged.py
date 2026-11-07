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


def module_names(root=Path("/"), release=None):
    """Use the installed running kernel's Gunyah module directory, not board guesses."""
    release = release or os.uname().release
    base = root / "lib/modules" / release
    names = []
    try:
        dependencies = (base / "modules.dep").read_text().splitlines()
    except FileNotFoundError:
        return []  # built-in kernels need no modprobe
    for line in dependencies:
        path = line.split(":", 1)[0]
        if "/drivers/virt/gunyah/" not in "/" + path:
            continue
        name = Path(path).name.split(".ko", 1)[0].replace("-", "_")
        # Only VM management/function drivers are requested. Dependencies come
        # from modprobe; optional platform/trusted-VM hooks are kernel policy.
        if name in host.MODULES and not (root / "sys/module" / name).exists():
            names.append(name)
    return sorted(set(names))


def load():
    if Path("/etc/initrd-release").exists():
        raise ValueError("cannot load VM management drivers in initrd")
    host.firmware().require()
    names = module_names()
    if names:
        run(["/usr/sbin/modprobe", "--all", *names])
    run(["/usr/bin/udevadm", "settle", "--timeout=10"])
    if not Path("/dev/gunyah").is_char_device():
        raise ValueError("running kernel does not provide /dev/gunyah")


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
    if acceleration == "vhost":
        run(["/usr/sbin/modprobe", "vhost_net"])
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


def launch(account, store, name, wayland):
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
    fw = host.firmware().require()
    limit = host.memory_budget()["memlock_mib"] * host.MIB
    if limit < 768 * host.MIB:
        raise ValueError("insufficient host RAM")
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


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in ("load", "net-up", "net-down", "run"):
        raise ValueError("unknown fixed action")
    action = argv[0]
    if len(argv) != (1 if action == "load" else 4):
        raise ValueError("invalid fixed action arguments")
    account = caller()
    if action == "load":
        return load()
    if action == "run":
        return launch(account, *argv[1:])
    return network(action, account, *argv[1:])
