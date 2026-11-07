# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""Read-only source image format recognition, reused by the GTK wizard."""


def image_format(path):
    """raw | qcow2 | iso | zstd | xz | unknown"""
    with open(path, "rb") as f:
        head = f.read(8)
        f.seek(0x8001)
        iso = f.read(5)
    if head[:4] == b"QFI\xfb":
        return "qcow2"
    if head[:4] == b"\x28\xb5\x2f\xfd":
        return "zstd"
    if head[:6] == b"\xfd7zXZ\x00":
        return "xz"
    if iso == b"CD001":
        return "iso"
    return "raw"
