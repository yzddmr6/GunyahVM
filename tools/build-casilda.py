#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Build pinned Casilda Debian packages with the original upstream packaging.

Supply the archives listed in third_party/casilda/source.json. Build on the
target architecture with Debian's declared build dependencies installed.
No downloads or changes to system packages are performed by this script.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

REPO = Path(__file__).resolve().parents[1]
SOURCE = REPO / "third_party/casilda"


def verified(path, digest):
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != digest:
        raise ValueError("Input digest mismatch: " + path.name)
    return path


def build(inputs, output, jobs):
    lock = json.loads((SOURCE / "source.json").read_text())
    archives = [verified(inputs / name, value["sha256"]) for name, value in lock["sources"].items()]
    patches = [verified(SOURCE / name, digest) for name, digest in lock["patches"].items()]
    output.mkdir(parents=True, exist_ok=True)
    source = output / ("casilda-" + lock["version"])
    if source.exists():
        raise ValueError("Choose an output without an existing Casilda source tree")
    with tarfile.open(archives[0]) as archive:
        archive.extractall(output, filter="data")
    if not source.is_dir():
        raise ValueError("Unexpected upstream source directory")
    with tarfile.open(archives[1]) as archive:
        archive.extractall(source, filter="data")
    for patch in patches:
        subprocess.run(["patch", "--batch", "--fuzz=0", "-p1", "-i", str(patch)], cwd=source, check=True)
    changelog = source / "debian/changelog"
    changelog.write_text(
        f"casilda ({lock['package_version']}) unstable; urgency=medium\n\n"
        "  * Forward GTK touch sequences to native Wayland clients.\n"
        "  * Initialize output scale before accepting clients and update it on allocation.\n\n"
        " -- yzddmr6 <46088090+yzddmr6@users.noreply.github.com>  Fri, 13 Nov 2026 00:00:00 +0000\n\n"
        + changelog.read_text()
    )
    # Keep the original tarball next to the source, as dpkg expects.
    destination = output / archives[0].name
    if destination.resolve() != archives[0].resolve():
        shutil.copyfile(archives[0], destination)
    env = {**os.environ, "SOURCE_DATE_EPOCH": str(lock["source_date_epoch"]),
           "DEB_BUILD_OPTIONS": "parallel=" + str(jobs), "LC_ALL": "C.UTF-8"}
    subprocess.run(["dpkg-buildpackage", "-us", "-uc", "-b"], cwd=source, env=env, check=True)
    return sorted(output.glob("*" + lock["package_version"] + "*.deb"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=os.cpu_count())
    args = parser.parse_args()
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    for package in build(args.input_dir.resolve(), args.output.resolve(), args.jobs):
        print(package)
