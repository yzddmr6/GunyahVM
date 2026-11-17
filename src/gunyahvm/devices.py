# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Declarative DT profile matching; hardware capabilities never come from profiles."""

from copy import deepcopy
from pathlib import Path
import re
import tomllib

DIRECTORY = Path("/usr/share/gunyahvm/devices")
DEFAULT = {
    "id": "unknown",
    "name": "Unknown device",
    "compatible": [],
    "resources": {},
    "sensors": {"battery_names": []},
    "display": {"width": 1280, "height": 800},
}


def validate(profile):
    # Schema 1 profiles shipped these policy overrides before hardware discovery
    # supplied the limits. Continue loading their sensor and display selections.
    profile = dict(profile)
    for section, fields in (("resources", {"max_memory_mib", "max_cpus"}),
                            ("guards", {"min_start_percent", "stop_percent"})):
        legacy = profile.pop(section, {})
        if not isinstance(legacy, dict) or legacy.keys() - fields:
            raise ValueError(f"invalid profile {section}")
    if set(profile) - {*DEFAULT, "reasons", "schema"} or profile.get("schema") != 1:
        raise ValueError("invalid profile field or schema")
    if not re.fullmatch(r"[a-z0-9-]+", profile.get("id", "")):
        raise ValueError("invalid profile id")
    if not isinstance(profile.get("compatible"), list) or not profile["compatible"]:
        raise ValueError("profile needs DT compatibles")
    for name in profile["compatible"]:
        if not re.fullmatch(r"[a-zA-Z0-9,_.+-]+", name):
            raise ValueError("invalid DT compatible")
    out = deepcopy(DEFAULT)
    for section in ("resources", "sensors", "display"):
        values = profile.get(section, {})
        if not isinstance(values, dict) or set(values) - set(DEFAULT[section]):
            raise ValueError(f"invalid profile {section}")
        out[section].update(values)
    for section in ("resources", "display"):
        if any(type(v) is not int or v <= 0 for v in out[section].values()):
            raise ValueError("profile limits must be positive integers")
    for names in out["sensors"].values():
        if not isinstance(names, list) or any(
            not isinstance(n, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", n)
            for n in names
        ):
            raise ValueError("sensors must be exact sysfs names/types")
    for key in ("id", "name", "compatible", "reasons"):
        if key in profile:
            out[key] = profile[key]
    return out


def match(compatible, directory=DIRECTORY):
    for name in compatible:
        if not re.fullmatch(r"[a-zA-Z0-9,_.+-]+", name):
            raise ValueError("invalid DT compatible")
    profiles = [
        validate(tomllib.loads(p.read_text())) for p in sorted(directory.glob("*.toml"))
    ]
    for name in compatible:  # DT specificity order, never filesystem order
        matches = [p for p in profiles if name in p["compatible"]]
        if len(matches) > 1:
            raise ValueError("ambiguous device profiles for " + name)
        if matches:
            return matches[0]
    return deepcopy(DEFAULT)
