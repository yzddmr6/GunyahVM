# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""Versioned VM model. No legacy import or implicit schema upgrades."""

from copy import deepcopy
import hashlib
import ipaddress
from pathlib import Path
import re
import uuid

SCHEMA = 2
GPU_MODES = ("none", "2d", "simplefb", "virgl", "venus", "native")
INPUT_MODES = ("tablet", "touchpad", "touch")


def network_identity(ident, uid):
    ident = str(uuid.UUID(ident))
    if type(uid) is not int or uid <= 0:
        raise ValueError("network needs an ordinary uid")
    token = hashlib.sha256(f"{uid}:{ident}".encode()).hexdigest()
    return {
        "tap": "gvm" + token[:10],
        "connection": f"gunyahvm-{uid}-{token[:10]}",
        "connection_uuid": str(uuid.uuid5(uuid.UUID(ident), "network-" + str(uid))),
        "mac": "02:" + ":".join(token[i : i + 2] for i in range(0, 10, 2)),
    }


def private_subnet(value):
    subnet = ipaddress.ip_network(value)
    allowed = [
        ipaddress.ip_network(s)
        for s in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    ]
    if (
        subnet.version != 4
        or subnet.prefixlen != 24
        or not any(subnet.subnet_of(n) for n in allowed)
    ):
        raise ValueError("requires RFC1918 IPv4 /24")
    return subnet


def display_defaults(windows=False):
    return {
        "gpu": "simplefb" if windows else "none",
        "width": 1280,
        "height": 720 if windows else 800,
        "dpi": 0,
        "keyboard": True,
        "mouse": True,
        "input_mode": "tablet",
        "fullscreen": False,
        "keyboard_grab": False,
        "output": "",
        "vnc": windows,
        "vnc_port": 5900,
    }


def complete_new(cfg):
    """Called only by creation, never by loading a stored VM."""
    cfg = deepcopy(cfg)
    cfg["schema"] = SCHEMA
    cfg.setdefault("os", "linux")
    cfg.update(network_identity(cfg["id"], cfg["uid"]))
    cfg.setdefault("backend", "crosvm")
    cfg.setdefault("memory_mode", "protected")
    cfg.setdefault("display", display_defaults(cfg["os"] == "windows11"))
    cfg.setdefault("shares", [])
    cfg.setdefault("forwards", [])
    cfg.setdefault("network_mode", "nat")
    cfg.setdefault("windows", {"ssh_user": "gunyahvm-agent"})
    cfg.setdefault("guest_payload", False)
    cfg.setdefault("guest_distribution", "")
    cfg.setdefault("ssh_public_key", "")
    return cfg


def integer(data, key, lo, hi):
    value = data.get(key)
    if type(value) is not int or not lo <= value <= hi:
        raise ValueError(f"{key} must be an integer {lo}..{hi}")


def validate(cfg):
    if (
        not isinstance(cfg, dict)
        or type(cfg.get("schema")) is not int
        or cfg["schema"] != SCHEMA
    ):
        raise ValueError(f"VM requires schema {SCHEMA}; recreate old test VMs")
    known = {
        "schema",
        "id",
        "uid",
        "gid",
        "name",
        "cpus",
        "memory_mib",
        "disk_gib",
        "os",
        "backend",
        "memory_mode",
        "display",
        "shares",
        "forwards",
        "network_mode",
        "windows",
        "guest_payload",
        "guest_distribution",
        "subnet",
        "vhost_net",
        "username",
        "source_image",
        "boot_managed",
        "ssh_identity",
        "ssh_public_key",
        "tap",
        "connection",
        "connection_uuid",
        "mac",
        "install_media",
        "windows_edition",
    }
    if set(cfg) - known:
        raise ValueError(
            "unknown VM configuration fields: " + ", ".join(sorted(set(cfg) - known))
        )
    for flag in ("boot_managed", "install_media"):
        if flag in cfg and type(cfg[flag]) is not bool:
            raise ValueError(flag + " must be boolean")
    for key in ("name", "id", "subnet", "backend", "os", "memory_mode", "network_mode"):
        if not isinstance(cfg.get(key), str):
            raise ValueError(key + " must be a string")
    if cfg.get("backend") != "crosvm" or cfg.get("os") not in ("linux", "windows11"):
        raise ValueError("unsupported backend or guest OS")
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", cfg.get("name", "")):
        raise ValueError("invalid VM name")
    integer(cfg, "uid", 1, 2**31 - 1)
    integer(cfg, "gid", 1, 2**31 - 1)
    if str(uuid.UUID(cfg["id"])) != cfg["id"]:
        raise ValueError("VM id must be a canonical UUID")
    expected = network_identity(cfg["id"], cfg["uid"])
    if any(cfg.get(k) != v for k, v in expected.items()):
        raise ValueError("network identity mismatch")
    private_subnet(cfg["subnet"])
    integer(cfg, "cpus", 1, 64)
    integer(cfg, "memory_mib", 4608 if cfg["os"] == "windows11" else 512, 65536)
    integer(cfg, "disk_gib", 64 if cfg["os"] == "windows11" else 1, 2048)
    if cfg.get("memory_mode") not in ("protected", "shared"):
        raise ValueError("invalid memory mode")
    if (
        cfg.get("network_mode") not in ("nat", "off")
        or type(cfg.get("vhost_net")) is not bool
    ):
        raise ValueError("invalid network mode/vhost-net")
    d = cfg.get("display", {})
    if (
        not isinstance(d, dict)
        or set(d) != set(display_defaults())
        or d["gpu"] not in GPU_MODES
    ):
        raise ValueError("invalid display settings")
    for key, lo, hi in [
        ("width", 640, 3840),
        ("height", 480, 3000),
        ("dpi", 0, 600),
        ("vnc_port", 1024, 65535),
    ]:
        integer(d, key, lo, hi)
    for key in ("keyboard", "mouse", "fullscreen", "keyboard_grab", "vnc"):
        if type(d[key]) is not bool:
            raise ValueError("display." + key + " must be boolean")
    if d["vnc"] and d["gpu"] == "none":
        raise ValueError("VNC requires a display mode")
    if (
        d["input_mode"] not in INPUT_MODES
        or not isinstance(d["output"], str)
        or not re.fullmatch(r"[A-Za-z0-9_.-]{0,64}", d["output"])
    ):
        raise ValueError("invalid input mode/output")
    if cfg["memory_mode"] == "shared" and (
        d["gpu"] == "native" or cfg["os"] == "windows11"
    ):
        raise ValueError("shared memory is unavailable with native graphics or Windows")
    if cfg["os"] == "windows11" and type(cfg.get("install_media")) is not bool:
        raise ValueError("Windows needs an installation-media flag")
    if cfg["os"] == "windows11" and (d["gpu"] != "simplefb" or not d["vnc"]):
        raise ValueError("Windows requires the loopback simplefb VNC display")
    if not isinstance(cfg.get("ssh_public_key", ""), str) or any(
        c in cfg.get("ssh_public_key", "") for c in "\r\n"
    ):
        raise ValueError("SSH public key must be a single line")
    if type(cfg.get("guest_payload")) is not bool:
        raise ValueError("invalid guest payload flag")
    if not isinstance(cfg.get("shares"), list) or len(cfg["shares"]) > 4:
        raise ValueError("at most four shared folders")
    tags = set()
    for share in cfg["shares"]:
        if (
            not isinstance(share, dict)
            or set(share) != {"path", "tag", "ro"}
            or type(share["ro"]) is not bool
        ):
            raise ValueError("invalid shared folder")
        path, tag = share["path"], share["tag"]
        if (
            not isinstance(path, str)
            or not Path(path).is_absolute()
            or any(c in path for c in ":,\n\0")
        ):
            raise ValueError(
                "shared folder requires an absolute path without parameter separators"
            )
        if (
            not isinstance(tag, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", tag)
            or tag in tags
            or tag == "gvmtools"
        ):
            raise ValueError("duplicate or invalid shared folder tag")
        tags.add(tag)
    if not isinstance(cfg.get("forwards"), list) or len(cfg["forwards"]) > 16:
        raise ValueError("at most sixteen forwards")
    ports = set()
    for forward in cfg["forwards"]:
        if (
            not isinstance(forward, dict)
            or set(forward) != {"proto", "host", "guest"}
            or forward["proto"] not in ("tcp", "udp")
        ):
            raise ValueError("invalid forward")
        integer(forward, "host", 1024, 65535)
        integer(forward, "guest", 1, 65535)
        key = (forward["proto"], forward["host"])
        if key in ports:
            raise ValueError("duplicate forwarded port")
        ports.add(key)
    if not isinstance(cfg.get("windows"), dict) or not isinstance(
        cfg["windows"].get("ssh_user"), str
    ):
        raise ValueError("invalid Windows settings")
    if not re.fullmatch(
        r"[a-zA-Z_][a-zA-Z0-9_.-]{0,31}", cfg.get("windows", {}).get("ssh_user", "")
    ):
        raise ValueError("invalid Windows SSH user")
    if cfg["os"] == "linux" and (
        not isinstance(cfg.get("username"), str)
        or not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", cfg["username"])
    ):
        raise ValueError("invalid Linux guest user")
    return cfg
