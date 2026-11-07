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

from prebuilt_inputs import REPO, sha


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crosvm", type=Path, required=True)
    parser.add_argument("--firmware", type=Path, required=True)
    parser.add_argument("--winpe-helper", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
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
