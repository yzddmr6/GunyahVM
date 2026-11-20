# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The macOS system disk: GPT layout, and old DroidVMDataStore disks converted in place."""
import json
import os
import struct
import subprocess
import uuid
import zlib

import pytest
from gunyahvm import macos_disk as d
from gunyahvm.macos_vendor import grow_data_store

PAGE = 4096


def store(path, logical, present):
    """A DroidVMDataStore as GunyahVM 1.5.0 made it, with `present` {page: bytes} written and marked."""
    pages = logical // PAGE
    bitmap = (pages + 8 * PAGE - 1) // (8 * PAGE)
    offset = (2 + bitmap) * PAGE
    marks = bytearray(bitmap * PAGE)
    for page in present:
        marks[page // 8] |= 1 << (page % 8)
    with open(path, "xb") as f:
        f.truncate(offset + logical)
        f.write(b"DroidVMDataStore\x01\x00\x00\x00".ljust(PAGE, b"\0"))
        f.write(grow_data_store.header(pages, "00" * 32, 1))
        f.write(marks)
        for page, data in present.items():
            f.seek(offset + page * PAGE)
            f.write(data)
    return offset


def sgdisk_ok(path):
    """The GPT as another reader sees it (sfdisk, when the host has it)."""
    tool = subprocess.run(["sh", "-c", "command -v sfdisk"], capture_output=True, text=True).stdout.strip()
    if not tool:
        return None
    out = subprocess.run([tool, "--json", str(path)], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)["partitiontable"]


def test_new_disk_layout(tmp_path):
    logical = 64 << 20
    disk = tmp_path / "disk.raw"
    assert d.create(disk, logical) == 1 << 20
    size = disk.stat().st_size
    assert size == (1 << 20) + logical + (1 << 20)
    assert disk.stat().st_blocks * 512 < 1 << 20  # sparse
    assert d.container(disk) == (1 << 20, logical)
    data = disk.read_bytes()
    assert data[510:512] == b"\x55\xaa" and data[450] == 0xEE
    assert data[-512:-504] == b"EFI PART"  # the backup header
    table = sgdisk_ok(disk)
    if table:
        part, = table["partitions"]
        assert table["label"] == "gpt" and part["start"] == 2048 and part["size"] == logical // 512
        assert part["type"].upper() == str(d.TYPE).upper()


def test_a_damaged_header_is_refused(tmp_path):
    disk = tmp_path / "disk.raw"
    d.create(disk, 16 << 20)
    with open(disk, "r+b") as f:
        f.seek(512 + 40)
        f.write(b"\xff")
    with pytest.raises(d.Invalid):
        d.container(disk)


def test_store_converts_in_place_and_reads_the_same(tmp_path):
    logical = 512 << 20  # the smallest real disk is 40 GiB; its store header fits a GPT before the container
    disk = tmp_path / "disk.raw"
    offset = store(disk, logical, {0: b"NXSB".ljust(PAGE, b"x"), 9: b"nine".ljust(PAGE, b"9")})
    # A page written and then dropped from the bitmap reads as zero in the store.
    with open(disk, "r+b") as f:
        f.seek(offset + 5 * PAGE)
        f.write(b"stale".ljust(PAGE, b"s"))
    inode = disk.stat().st_ino
    journal = tmp_path / "disk.convert.json"
    assert d.convert(disk, journal) == (offset, logical)
    assert not journal.exists()
    assert disk.stat().st_ino == inode
    assert d.container(disk) == (offset, logical)
    with open(disk, "rb") as f:
        f.seek(offset)
        view = f.read(logical)
    assert view[:4] == b"NXSB" and view[9 * PAGE:9 * PAGE + 4] == b"nine"
    assert view[5 * PAGE:6 * PAGE] == bytes(PAGE)
    assert view[PAGE:5 * PAGE] == bytes(4 * PAGE)
    with open(disk, "rb") as f:
        assert f.read(16) != b"DroidVMDataStore"
        f.seek(34 * 512)
        assert f.read(offset - 34 * 512) == bytes(offset - 34 * 512)
    assert d.convert(disk, journal) == (offset, logical)  # already a GPT disk: nothing changes


def test_an_interrupted_conversion_completes(tmp_path, monkeypatch):
    logical = 512 << 20
    disk = tmp_path / "disk.raw"
    offset = store(disk, logical, {1: b"one".ljust(PAGE, b"1")})
    journal = tmp_path / "disk.convert.json"
    real = os.pwrite
    calls = []

    def crash_after_backup(fd, data, at):
        calls.append(at)
        if len(calls) == 2:  # the primary header: as if the host lost power before it
            with open(disk, "r+b") as f:  # half of it reached the disk
                f.write(data[:700])
            raise OSError("power lost")
        return real(fd, data, at)

    monkeypatch.setattr(os, "pwrite", crash_after_backup)
    with pytest.raises(OSError):
        d.convert(disk, journal)
    monkeypatch.setattr(os, "pwrite", real)
    assert json.loads(journal.read_text()) == {"offset": offset, "logical": logical}
    assert d.convert(disk, journal) == (offset, logical)
    assert d.container(disk) == (offset, logical)
    with open(disk, "rb") as f:
        f.seek(offset + PAGE)
        assert f.read(3) == b"one"


def test_unknown_disks_are_refused(tmp_path):
    disk = tmp_path / "disk.raw"
    disk.write_bytes(bytes(3 * PAGE))
    with pytest.raises(d.Invalid):
        d.convert(disk, tmp_path / "j.json")
