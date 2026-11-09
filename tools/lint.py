#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Offline syntax, metadata, documentation-link and pinned-input validation."""

import ast
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def files():
    tracked = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
    )
    return sorted(
        {
            ROOT / name.decode()
            for name in tracked.split(b"\0")
            if name and (ROOT / name.decode()).is_file()
        }
    )


def main():
    errors = []
    paths = files()
    for path in paths:
        rel = path.relative_to(ROOT)
        data = path.read_bytes()
        doc_image = rel.parent == Path("docs/images") and path.suffix == ".png"
        if doc_image and (
            not data.startswith(b"\x89PNG\r\n\x1a\n")
            or not data.endswith(b"\x00\x00\x00\x00IEND\xaeB`\x82")
            or len(data) > 1024 * 1024
        ):
            errors.append(
                f"{rel}: documentation PNG needs PNG boundaries and must be at most 1 MiB"
            )
        if b"\0" in data and not doc_image:
            errors.append(f"{rel}: binary payload belongs in release inputs, not Git")
        if (
            path.suffix != ".license"
            and b"SPDX-License-Identifier:" not in data[:8192]
            and not path.with_name(path.name + ".license").is_file()
        ):
            errors.append(f"{rel}: missing SPDX header or sidecar")
        if (
            path.suffix == ".py"
            or rel.parts[0] == "libexec"
            or rel == Path("bin/gunyahvm")
            or rel == Path("src/gunyahvm/guest-graphics-run")
        ):
            try:
                ast.parse(data.decode(), filename=str(rel))
            except SyntaxError as e:
                errors.append(f"{rel}: {e}")
        if path.suffix == ".json":
            try:
                json.loads(data)
            except ValueError as e:
                errors.append(f"{rel}: {e}")
        if path.suffix == ".policy":
            ET.fromstring(data)
        if path.suffix == ".md":
            for link in re.findall(r"\]\(([^)]+)\)", data.decode()):
                target = link.split("#", 1)[0]
                if (
                    target
                    and not re.match(r"[a-z]+://", target)
                    and not (path.parent / target).exists()
                ):
                    errors.append(f"{rel}: broken local link {target}")
    for path in (ROOT / "debian").iterdir():
        if path.read_bytes().startswith(b"#!/bin/sh"):
            subprocess.run(["sh", "-n", str(path)], check=True)
    lock = json.loads((ROOT / "tools/assets.lock.json").read_text())["files"]
    actual = {
        str(p.relative_to(ROOT / "assets"))
        for p in (ROOT / "assets").rglob("*")
        if p.is_file()
    }
    if actual != set(lock):
        errors.append("assets: inventory mismatch")
    for name, digest in lock.items():
        path = ROOT / "assets" / name
        if (
            not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest
        ):
            errors.append("assets: hash mismatch " + name)
    crosvm = json.loads((ROOT / "tools/crosvm.lock.json").read_text())
    for patch in crosvm["patches"]:
        path = ROOT / patch["file"]
        if hashlib.sha256(path.read_bytes()).hexdigest() != patch["sha256"]:
            errors.append("patch hash mismatch: " + patch["file"])
        if not all(
            x in path.read_text() for x in ("Purpose:", "Origin:", "Upstream-Status:")
        ):
            errors.append("patch lacks provenance: " + patch["file"])
    series = [
        x
        for x in (ROOT / "third_party/crosvm/patches/series").read_text().splitlines()
        if x and not x.startswith("#")
    ]
    if series != [Path(x["file"]).name for x in crosvm["patches"]]:
        errors.append("patch series order differs from lock")
    runtime_sources = json.loads(
        (ROOT / "third_party/runtime/sources.json").read_text()
    )
    for family in runtime_sources["patches"].values():
        for item in family:
            if (
                hashlib.sha256((ROOT / item["file"]).read_bytes()).hexdigest()
                != item["sha256"]
            ):
                errors.append("runtime patch differs: " + item["file"])
    subprocess.run(["sh", "-n", str(ROOT / "tools/build-guest-initrd.sh")], check=True)
    for english in [
        ROOT / "README.md",
        ROOT / "CONTRIBUTING.md",
        ROOT / "SECURITY.md",
        ROOT / "CHANGELOG.md",
        *(ROOT / "docs").glob("*.md"),
    ]:
        if ".zh-CN." in english.name:
            continue
        chinese = english.with_name(english.stem + ".zh-CN.md")
        if not chinese.is_file():
            errors.append(
                "missing Chinese counterpart: " + str(english.relative_to(ROOT))
            )
        elif [
            len(x.split()[0])
            for x in english.read_text().splitlines()
            if x.startswith("#")
        ] != [
            len(x.split()[0])
            for x in chinese.read_text().splitlines()
            if x.startswith("#")
        ]:
            errors.append("document heading structure differs: " + english.name)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print(
        f"Validated {len(paths)} files, SPDX annotations, syntax, links, paired documents and pinned inputs."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
