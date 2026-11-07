#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Bundle maintained source and checksum-verified upstream/Cargo source inputs."""

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import tomllib

REPO = Path(__file__).resolve().parents[1]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def import_sources(inputs, cargo_cache, destination, lock):
    for filename in ("crosvm-96e84c3.tar.gz", "minijail-7845d899.tar.gz"):
        source = inputs / filename
        if digest(source) != lock["inputs"][filename]:
            raise ValueError("Upstream source checksum differs: " + filename)
        target = destination / "inputs" / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    cargo_lock = tomllib.loads((REPO / "third_party/crosvm/Cargo.lock").read_text())
    crates = {
        f"{p['name']}-{p['version']}.crate": p["checksum"]
        for p in cargo_lock["package"]
        if "checksum" in p
    }
    cache_lock = json.loads((REPO / "tools/cargo-sources.lock.json").read_text())[
        "files"
    ]
    for name, expected in cache_lock.items():
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Invalid Cargo cache lock path")
        source = cargo_cache / relative
        if digest(source) != expected:
            raise ValueError("Cargo source checksum differs: " + name)
        if relative.suffix == ".crate" and crates.get(relative.name) != expected:
            raise ValueError("Cargo source differs from Cargo.lock: " + name)
        target = destination / "cargo-cache" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)


def export_git(tree, commit, output):
    actual = subprocess.check_output(
        ["git", "-C", str(tree), "rev-parse", "HEAD"], text=True
    ).strip()
    if actual != commit:
        raise ValueError("Source commit mismatch: " + tree.name)
    output.mkdir(parents=True, exist_ok=True)
    producer = subprocess.Popen(
        ["git", "-C", str(tree), "archive", commit], stdout=subprocess.PIPE
    )
    with tarfile.open(fileobj=producer.stdout, mode="r|") as archive:
        archive.extractall(output, filter="data")
    producer.stdout.close()
    if producer.wait():
        raise ValueError("Cannot export source: " + tree.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--cargo-cache", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--windows-sources", type=Path)
    parser.add_argument("--driver-sources", type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output already exists")
    lock = json.loads((REPO / "tools/crosvm.lock.json").read_text())
    with tempfile.TemporaryDirectory(prefix="gunyahvm-source-") as directory:
        root = Path(directory) / "gunyahvm-1.0.0"
        root.mkdir()
        import_sources(args.inputs, args.cargo_cache, root, lock)
        names = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=REPO,
        ).split(b"\0")
        for name in sorted(set(names)):
            if not name:
                continue
            path = Path(name.decode())
            if not (REPO / path).is_file():
                continue
            target = root / path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(REPO / path, target)
            target.chmod((REPO / path).stat().st_mode & 0o777)
        winlock = json.loads((REPO / "tools/windows.lock.json").read_text())
        if args.windows_sources:
            for source in winlock["firmware_repositories"]:
                export_git(
                    args.windows_sources / source["path"],
                    source["commit"],
                    root / "windows-sources" / source["path"],
                )
        if args.driver_sources:
            export_git(
                args.driver_sources,
                winlock["drivers"]["source_commit"],
                root / "driver-sources",
            )
        (root / "SOURCE-CONTENTS.txt").write_text(
            "SPDX-License-Identifier: GPL-2.0-only\nCopyright (C) 2026 yzddMr6\n\n"
            "GunyahVM 1.0.0 corresponding source: maintained Python/C code, patches,\n"
            "crosvm/minijail source archives, Cargo registry source packages and metadata.\n"
            "Use docs/building.md to fetch locked compiler/sysroot inputs and build.\n"
            "Binary assets are omitted; use --crosvm-build and --windows-build to package.\n"
            "Permissively licensed firmware/driver source repositories are pinned in\n"
            "tools/windows.lock.json and fetched by tools/fetch-windows-inputs.py.\n"
            f"Firmware source trees included: {bool(args.windows_sources)}\n"
            f"Driver source tree included: {bool(args.driver_sources)}\n"
        )
        paths = [p for p in sorted(root.rglob("*")) if p.is_file()]
        (root / "SOURCE-SHA256SUMS").write_text(
            "".join(digest(p) + "  " + str(p.relative_to(root)) + "\n" for p in paths)
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "tar",
                "--sort=name",
                "--mtime=@1794009600",
                "--owner=0",
                "--group=0",
                "--numeric-owner",
                "--mode=u+rwX,go+rX,go-w",
                "-I",
                "zstd -T0 -3",
                "-cf",
                str(args.output.resolve()),
                "-C",
                directory,
                root.name,
            ],
            check=True,
        )
    print(digest(args.output) + "  " + args.output.name)


if __name__ == "__main__":
    main()
