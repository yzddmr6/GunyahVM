#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Recreate the GunyahVM release input attachment from locked binaries and their notices."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import tempfile

from prebuilt_inputs import REPO, fetch, sha


def bundle_cache(cache, output, runtime_output):
    """Publish complete offline assembly inputs without changing pinned payloads."""
    prebuilt = json.loads((REPO / "tools/prebuilt.lock.json").read_text())["release"]
    windows = json.loads((REPO / "tools/windows.lock.json").read_text())
    runtime = json.loads((REPO / "tools/runtime.lock.json").read_text())["input"]
    for destination in (output, runtime_output):
        if destination.exists():
            raise ValueError("Output already exists: " + str(destination))
    if runtime_output.name == runtime["filename"]:
        # A runtime archive new in this release is published as itself, so the locked URL resolves.
        runtime_output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(fetch(runtime, cache, offline=True), runtime_output)
        print(sha(runtime_output) + "  " + runtime_output.name)
        bundles = [(output, [prebuilt, windows["drivers"], windows["openssh"]])]
    else:
        bundles = [(output, [prebuilt, windows["drivers"], windows["openssh"]]),
                   (runtime_output, [runtime])]
    for destination, specs in bundles:
        with tempfile.TemporaryDirectory(prefix="gunyahvm-release-inputs-") as tmp:
            root = Path(tmp)
            for spec in specs:
                source = fetch(spec, cache, offline=True)
                target = root / "inputs/prebuilt" / spec["filename"]
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
            (root / "README.inputs.txt").write_text(
                "Extract the input attachments in the extracted GunyahVM source directory.\n"
                "They populate inputs/prebuilt with the exact locked archives and notices.\n"
                "Older filenames identify immutable payloads reused by this release.\n"
                "A runtime attachment named as in tools/runtime.lock.json is that archive\n"
                "itself: copy it into inputs/prebuilt unchanged.\n"
                "Build without network: python3 tools/build-virtualization-debs.py "
                "--input-cache inputs/prebuilt --offline --output dist\n"
                "The Windows ISO is user-supplied guest media, not a package build input.\n"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["tar", "--sort=name", "--mtime=@1794009600", "--owner=0",
                 "--group=0", "--numeric-owner", "--mode=u+rwX,go+rX,go-w",
                 "-I", "zstd -T1 -19", "-cf", str(destination.resolve()),
                 "-C", str(root), "README.inputs.txt", "inputs"], check=True,
            )
        print(sha(destination) + "  " + destination.name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-cache", type=Path)
    parser.add_argument("--runtime-output", type=Path)
    parser.add_argument("--crosvm", type=Path)
    parser.add_argument("--firmware", type=Path)
    parser.add_argument("--winpe-helper", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.input_cache:
        if not args.runtime_output or any((args.crosvm, args.firmware, args.winpe_helper)):
            parser.error("--input-cache requires --runtime-output and no individual binary inputs")
        bundle_cache(args.input_cache, args.output, args.runtime_output)
        return
    if args.runtime_output or not all((args.crosvm, args.firmware, args.winpe_helper)):
        parser.error("provide --input-cache/--runtime-output or all three individual binary inputs")
    if args.output.exists():
        parser.error("output already exists")
    lock = json.loads((REPO / "tools/prebuilt.lock.json").read_text())["release"]
    sources = {
        "crosvm": args.crosvm,
        "windows/firmware.fd": args.firmware,
        "windows/winpe-helper.exe": args.winpe_helper,
    }
    for name in ["NOTICE", "LICENSE", "LICENSE.license"]:
        sources["notices/" + name] = REPO / name
    for directory, prefix in [
        (REPO / "LICENSES", "LICENSES"),
        (REPO / "assets/licenses", "crosvm"),
        (REPO / "assets/windows/licenses", "windows"),
    ]:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                sources[
                    "notices/" + prefix + "/" + str(path.relative_to(directory))
                ] = path
    if set(sources) != set(lock["files"]):
        raise ValueError("Input/notice inventory differs from release lock")
    with tempfile.TemporaryDirectory(prefix="gunyahvm-inputs-") as tmp:
        root = Path(tmp)
        for name, source in sources.items():
            spec = lock["files"][name]
            if sha(source) != spec["sha256"] or source.stat().st_size != spec["size"]:
                raise ValueError("Input differs from release lock: " + name)
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        archive = root / "result.tar.zst"
        subprocess.run(
            [
                "tar",
                "--sort=name",
                "--mtime=@1794009600",
                "--owner=0",
                "--group=0",
                "--numeric-owner",
                "--mode=0644",
                "-I",
                "zstd -T1 -19",
                "-cf",
                str(archive),
                "-C",
                str(root),
                *sources,
            ],
            check=True,
        )
        if sha(archive) != lock["sha256"]:
            raise ValueError(
                "Archive differs from release lock; use GNU tar and zstd 1.5.5"
            )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(archive, args.output)
    print(sha(args.output) + "  " + args.output.name)


if __name__ == "__main__":
    main()
