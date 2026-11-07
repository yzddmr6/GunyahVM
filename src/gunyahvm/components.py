# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Versioned runtime package contract. No subprocesses or inferred build features."""

import json
import re
from pathlib import Path

MANIFEST = Path("/usr/share/gunyahvm/runtime.json")
PREFIXES = ("/usr/lib/gunyahvm/", "/usr/share/gunyahvm/")


def validate(data, root=Path("/"), check_files=True):
    if (
        not isinstance(data, dict)
        or type(data.get("schema")) is not int
        or data.get("schema") != 1
        or data.get("architecture") != "arm64"
    ):
        raise ValueError("runtime manifest requires schema 1 and architecture arm64")
    components = data.get("components")
    if not isinstance(components, dict) or "crosvm" not in components:
        raise ValueError("runtime manifest requires crosvm")
    allowed = {
        "crosvm",
        "renderer",
        "guest_kernel",
        "guest_mesa",
        "uefi",
        "windows_drivers",
    }
    if set(components) - allowed:
        raise ValueError("unknown runtime component")
    for name, item in components.items():
        if (
            not isinstance(item, dict)
            or not isinstance(item.get("version"), str)
            or not item["version"]
        ):
            raise ValueError(f"{name}: missing version")
        if not isinstance(item.get("features", []), list) or any(
            not isinstance(f, str) for f in item.get("features", [])
        ):
            raise ValueError(f"{name}: features must be strings")
        files = item.get("files", {})
        if not isinstance(files, dict) or not files:
            raise ValueError(f"{name}: missing payload files")
        for role, path in files.items():
            if (
                not isinstance(path, str)
                or not path.startswith(PREFIXES)
                or ".." in Path(path).parts
                or any(c in path for c in "\n\0,:")
            ):
                raise ValueError(f"{name}: invalid installed path")
            file = root / path.lstrip("/")
            if check_files and not file.is_file():
                raise ValueError(f"{name}: missing payload {role}: {path}")
            if check_files and not file.resolve().is_relative_to(
                root.resolve() / "usr"
            ):
                raise ValueError(f"{name}: payload escapes installation")
    required = {
        "crosvm": {"binary"},
        "renderer": {"library"},
        "guest_kernel": {"image", "initrd"},
        "guest_mesa": {"archive"},
        "uefi": {"image"},
        "windows_drivers": {"inventory", "certificate"},
    }
    for name, item in components.items():
        if not required[name] <= item["files"].keys():
            raise ValueError(name + ": missing required payload role")
    renderer = components.get("renderer", {})
    if "venus" in renderer.get("features", []) and "render_server" not in renderer.get(
        "files", {}
    ):
        raise ValueError("renderer: Venus requires its private render_server")
    if components["crosvm"]["files"]["binary"] != "/usr/lib/gunyahvm/crosvm/crosvm":
        raise ValueError(
            "crosvm must use the installed executable path covered by host policy"
        )
    if "guest_kernel" in components:
        kernel = components["guest_kernel"]
        if not re.fullmatch(r"[A-Za-z0-9_.+-]+", kernel.get("kernel_release", "")):
            raise ValueError("guest_kernel: invalid kernel_release")
        if not isinstance(kernel.get("modules_load", []), list) or any(
            not isinstance(m, str) or not re.fullmatch(r"[A-Za-z0-9_]+", m)
            for m in kernel.get("modules_load", [])
        ):
            raise ValueError("guest_kernel: invalid module name")
    if "guest_mesa" in components:
        mesa = components["guest_mesa"]
        if not re.fullmatch(r"[a-z0-9_-]+:[a-zA-Z0-9_.-]+", mesa.get("guest_abi", "")):
            raise ValueError(
                "guest_mesa: guest_abi must identify the guest OS and version"
            )
        for key in ("library_subdir", "icd"):
            path = mesa.get(key, "")
            if (
                not isinstance(path, str)
                or not path
                or Path(path).is_absolute()
                or ".." in Path(path).parts
            ):
                raise ValueError("guest_mesa: invalid private-prefix path")
    return data


def load(path=MANIFEST, root=Path("/")):
    return validate(json.loads(path.read_text()), root)


def features(manifest, component):
    return set(manifest.get("components", {}).get(component, {}).get("features", []))


def payload(manifest, component, role):
    return Path(manifest["components"][component]["files"][role])
