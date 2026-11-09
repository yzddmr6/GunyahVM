# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Stage the unchanged, hash-locked upstream OpenSSH archive and its notices."""

import hashlib
import json
import shutil
import zipfile


def verify_archive(archive, spec):
    if spec["filename"] != "OpenSSH-ARM64.zip" or spec["architecture"] != "arm64":
        raise ValueError("OpenSSH requires the locked ARM64 archive")
    if not archive.is_file():
        raise ValueError("Missing offline OpenSSH archive: " + str(archive))
    with archive.open("rb") as stream:
        if hashlib.file_digest(stream, "sha256").hexdigest() != spec["sha256"]:
            raise ValueError("Offline OpenSSH checksum mismatch: " + str(archive))


def stage_openssh(archive, destination, spec):
    verify_archive(archive, spec)
    destination.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(archive, destination / spec["filename"])
    (destination / "openssh.json").write_text(json.dumps(spec, indent=2) + "\n")
    notices = destination / "licenses/openssh"
    notices.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive) as package:
        for name in ("LICENSE.txt", "NOTICE.txt"):
            (notices / name).write_bytes(package.read("OpenSSH-ARM64/" + name))


def verify_openssh(directory, spec):
    if json.loads((directory / "openssh.json").read_text()) != spec:
        raise ValueError("Offline OpenSSH manifest differs from windows.lock.json")
    verify_archive(directory / spec["filename"], spec)
