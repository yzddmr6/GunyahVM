#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Assemble all release assets from HEAD and hash-addressed, locked inputs."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile

from prebuilt_inputs import GPU_REBUILDS, fetch, sha

REPO = Path(__file__).resolve().parents[1]


def read_lock(repo, name):
    return json.loads((repo / name).read_text())


def cached(item, cache):
    return fetch(item, cache / item["sha256"])


def build(cache):
    commit = subprocess.check_output(
        ["git", "-C", REPO, "rev-parse", "HEAD"], text=True
    ).strip()
    release = read_lock(REPO, "tools/release.lock.json")
    dist = REPO / "dist"
    dist.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".release-", dir=dist) as directory:
        work = Path(directory)
        source = work / "source"
        subprocess.run(
            ["git", "clone", "--quiet", "--shared", "--no-checkout", REPO, source],
            check=True,
        )
        subprocess.run(
            ["git", "-C", source, "checkout", "--quiet", "--detach", commit], check=True
        )
        version = (source / "src/gunyahvm/__init__.py").read_text().split(
            '__version__ = "', 1
        )[1].split('"', 1)[0]
        output = dist / version
        if output.exists():
            raise ValueError("Output already exists: " + str(output))
        print(f"Building {version} from {commit}", flush=True)
        env = os.environ | {"TMPDIR": str(work), "PYTHONDONTWRITEBYTECODE": "1"}
        prebuilt = work / "prebuilt"
        prebuilt.mkdir()
        windows = read_lock(source, "tools/windows.lock.json")
        macos = read_lock(source, "tools/macos.lock.json")
        items = [
            read_lock(source, "tools/prebuilt.lock.json")["release"],
            read_lock(source, "tools/runtime.lock.json")["input"],
            *(windows[key] for key in ("drivers", "openssh", "gpu_driver", *GPU_REBUILDS)),
            macos["kit"]["input"],
        ]
        for item in items:
            shutil.copyfile(cached(item, cache), prebuilt / item["filename"])
        casilda = [(item, cached(item, cache)) for item in release["casilda"]]

        upstream = work / "upstream"
        upstream.mkdir()
        archive = cached(release["source_inputs"], cache)
        with archive.open("rb") as raw, subprocess.Popen(
            ["zstd", "-dcq"], stdin=raw, stdout=subprocess.PIPE
        ) as process:
            with tarfile.open(fileobj=process.stdout, mode="r|") as contents:
                for member in contents:
                    relative = Path(member.name).parts[1:]
                    if relative and relative[0] in {
                        "inputs", "cargo-cache", "runtime-sources", "macos-sources"
                    }:
                        member.name = str(Path(*relative))
                        contents.extract(member, upstream, filter="data")
            if process.wait():
                raise RuntimeError("Cannot unpack locked source inputs")

        packages = work / "packages"
        subprocess.run([
            sys.executable, "-B", source / "tools/build-virtualization-debs.py",
            "--input-cache", prebuilt, "--offline", "--output", packages,
        ], env=env, check=True)
        result = work / "result"
        result.mkdir()
        for path in [*packages.glob("*.deb"), packages / "manifest.json"]:
            path.rename(result / path.name)
        subprocess.run([
            sys.executable, "-B", source / "tools/bundle-virtualization-source.py",
            "--inputs", upstream / "inputs", "--cargo-cache", upstream / "cargo-cache",
            "--runtime-sources", upstream / "runtime-sources",
            "--macos-sources", upstream / "macos-sources",
            "--output", result / f"gunyahvm-{version}-source.tar.zst",
        ], env=env, check=True)
        # An input is this release's asset when its locked URL names this version's tag.
        published = [(item, path) for item, path in [
            (macos["kit"]["input"], prebuilt / macos["kit"]["input"]["filename"]),
            (read_lock(source, "tools/runtime.lock.json")["input"],
             prebuilt / read_lock(source, "tools/runtime.lock.json")["input"]["filename"]),
            (release["source_inputs"], archive), *casilda,
        ] if f"/v{version}/" in item.get("url", "")]
        for item, path in published:
            shutil.copyfile(path, result / item["filename"])
        names = [
            *sorted(p.name for p in result.glob("gunyahvm_*.deb")),
            "manifest.json", f"gunyahvm-{version}-source.tar.zst",
            *(item["filename"] for item, _ in published),
        ]
        (result / "SHA256SUMS").write_text(
            "".join(f"{sha(result / name)}  {name}\n" for name in names)
        )
        result.rename(output)
    print(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=Path.home() / ".cache/gunyahvm-build")
    args = parser.parse_args()
    os.umask(0o022)
    build(args.cache.expanduser().resolve())


if __name__ == "__main__":
    main()
