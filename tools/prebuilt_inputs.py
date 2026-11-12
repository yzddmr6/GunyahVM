# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Fetch and assemble pinned release inputs; never trust an unchecked cache."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import tempfile
import urllib.request
import zipfile

from windows_openssh import stage_openssh

REPO = Path(__file__).resolve().parents[1]


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fetch(item, cache, offline=False):
    """Verify both existing cache entries and downloads before returning a path."""
    cache.mkdir(parents=True, exist_ok=True)
    filename = item["filename"]
    if Path(filename).name != filename or filename in ("", ".", ".."):
        raise ValueError("Unsafe cache filename")
    target = cache / filename
    if target.exists():
        if sha(target) != item["sha256"]:
            raise ValueError("Cached input checksum mismatch: " + filename)
        return target
    if offline:
        raise ValueError("Missing offline input: " + filename)
    url = item.get("asset_url") or item.get("url")
    if not url:
        raise ValueError("Unpublished input must be provided in the local cache: " + filename)
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/octet-stream", "User-Agent": "gunyahvm-build"},
    )
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            dir=cache, prefix=filename + ".", delete=False
        ) as out:
            temporary = Path(out.name)
            with urllib.request.urlopen(request, timeout=90) as response:
                shutil.copyfileobj(response, out)
        if sha(temporary) != item["sha256"]:
            raise ValueError("Downloaded input checksum mismatch: " + filename)
        temporary.replace(target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return target


def safe_name(name):
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or str(path) != name or not path.parts:
        raise ValueError("Unsafe input path: " + name)
    return path


def unpack_release(archive, destination, item):
    """Accept exactly the locked regular members, with their sizes and hashes."""
    if sha(archive) != item["sha256"]:
        raise ValueError("Release input checksum mismatch")
    expected = item["files"]
    seen = set()
    with tempfile.TemporaryFile() as uncompressed:
        subprocess.run(["zstd", "-dc", str(archive)], stdout=uncompressed, check=True)
        uncompressed.seek(0)
        with tarfile.open(fileobj=uncompressed, mode="r:") as source:
            for member in source:
                name = safe_name(member.name)
                if (
                    member.name not in expected
                    or member.name in seen
                    or not member.isfile()
                ):
                    raise ValueError(
                        "Unexpected or duplicate release member: " + member.name
                    )
                spec = expected[member.name]
                if member.size != spec["size"]:
                    raise ValueError("Release member size mismatch: " + member.name)
                target = destination / name
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as stream, target.open("xb") as out:
                    shutil.copyfileobj(stream, out)
                if sha(target) != spec["sha256"]:
                    raise ValueError("Release member checksum mismatch: " + member.name)
                seen.add(member.name)
    if seen != set(expected):
        raise ValueError("Missing release members")


def unpack_drivers(archive, destination, item, inventory):
    if sha(archive) != item["sha256"]:
        raise ValueError("Driver archive checksum mismatch")
    with zipfile.ZipFile(archive) as source:
        names = source.namelist()
        for name, spec in inventory.items():
            path = safe_name(name)
            if names.count(name) != 1:
                raise ValueError("Missing or duplicate driver: " + name)
            info = source.getinfo(name)
            if info.is_dir() or info.file_size > 16 * 1024 * 1024:
                raise ValueError("Invalid driver member: " + name)
            data = source.read(info)
            if hashlib.sha256(data).hexdigest() != spec["sha256"]:
                raise ValueError("Driver checksum mismatch: " + name)
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as out:
                out.write(data)


def prepare(cache, destination, offline=False):
    """Assemble a temporary payload from verified downloads and text notices."""
    lock = json.loads((REPO / "tools/prebuilt.lock.json").read_text())["release"]
    drivers = json.loads((REPO / "tools/windows.lock.json").read_text())["drivers"]
    text_lock = json.loads((REPO / "tools/assets.lock.json").read_text())["files"]
    assets = REPO / "assets"
    actual = {str(p.relative_to(assets)) for p in assets.rglob("*") if p.is_file()}
    if actual != set(text_lock):
        raise ValueError("Text asset inventory differs from the lock")
    for name, digest in text_lock.items():
        if sha(assets / name) != digest:
            raise ValueError("Text asset checksum mismatch: " + name)
    own_archive = fetch(lock, cache, offline)
    driver_archive = fetch(drivers, cache, offline)
    openssh = json.loads((REPO / "tools/windows.lock.json").read_text())["openssh"]
    openssh_archive = fetch(openssh, cache, offline)
    destination.mkdir(parents=True, exist_ok=False)
    shutil.copytree(assets, destination, dirs_exist_ok=True)
    unpack_release(own_archive, destination, lock)
    inventory = json.loads((assets / "windows/driver-files.json").read_text())
    unpack_drivers(driver_archive, destination / "windows", drivers, inventory)
    stage_openssh(openssh_archive, destination / "windows", openssh)
    return destination
