# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Catalogue structure adapted from iceland-vms (mzakamin-hub)
# d628b9b25d08e074b34a2f1a6113ab23767b78d7; mirror selection inspired by
# librevm 56918558e2c4303cda803a655ea716869c551b71 (Chinese mirrors remain optional).
"""Small immutable-build image catalogue; checksums always come from the origin."""

import re

MIRRORS = {
    "official": "https://cloud-images.ubuntu.com",
    "tuna": "https://mirrors.tuna.tsinghua.edu.cn/ubuntu-cloud-images",
    "ustc": "https://mirrors.ustc.edu.cn/ubuntu-cloud-images",
}
ENTRIES = (
    {
        "id": "ubuntu-26.04",
        "name": "Ubuntu 26.04 ARM64",
        "distribution": "ubuntu",
        "filename": "ubuntu-26.04-server-cloudimg-arm64.img",
        "release": "20261016",
        "directory": "releases/resolute",
    },
)


def entries():
    return [dict(entry) for entry in ENTRIES]


def urls(ident, release=None, mirror="official"):
    entry = next((e for e in ENTRIES if e["id"] == ident), None)
    if entry is None or mirror not in MIRRORS:
        raise ValueError("unknown image or mirror")
    release = release or entry["release"]
    if not re.fullmatch(r"\d{8}", release):
        raise ValueError("release must identify an immutable YYYYMMDD image build")
    suffix = f"/{entry['directory']}/release-{release}/"
    return {
        "image": MIRRORS[mirror] + suffix + entry["filename"],
        "checksums": MIRRORS["official"] + suffix + "SHA256SUMS",
        "origin": MIRRORS["official"] + suffix + entry["filename"],
    }
