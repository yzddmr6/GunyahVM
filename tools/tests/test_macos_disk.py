# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The macOS system disk: GPT layout, and old DroidVMDataStore disks converted in place."""
import hashlib
import json
import os
import random
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


def store_view(path, offset, logical):
    """What the guest read from a DroidVMDataStore: present pages as stored, absent ones as zero."""
    pages = logical // PAGE
    with open(path, "rb") as f:
        f.seek(2 * PAGE)
        bitmap = f.read((pages + 8 * PAGE - 1) // (8 * PAGE) * PAGE)
        out = hashlib.sha256()
        for page in range(pages):
            if bitmap[page // 8] >> (page % 8) & 1:
                f.seek(offset + page * PAGE)
                out.update(f.read(PAGE))
            else:
                out.update(bytes(PAGE))
    return out.hexdigest()


def gpt_view(path, offset, logical):
    out = hashlib.sha256()
    with open(path, "rb") as f:
        f.seek(offset)
        for _ in range(logical // PAGE):
            out.update(f.read(PAGE))
    return out.hexdigest()


def test_store_converts_in_place_and_reads_the_same(tmp_path):
    logical = 512 << 20  # the smallest real disk is 40 GiB; its store header fits a GPT before the container
    disk = tmp_path / "disk.raw"
    rng = random.Random(7)
    present = {page: rng.randbytes(PAGE) for page in rng.sample(range(logical // PAGE), 300)}
    present[0] = b"NXSB".ljust(PAGE, b"x")
    offset = store(disk, logical, present)
    # A page written and then dropped from the bitmap reads as zero in the store.
    stale = next(p for p in range(1, 4096) if p not in present)
    with open(disk, "r+b") as f:
        f.seek(offset + stale * PAGE)
        f.write(b"stale".ljust(PAGE, b"s"))
    before = store_view(disk, offset, logical)
    inode = disk.stat().st_ino
    backup = tmp_path / "backup"
    assert d.convert(disk, backup) == (offset, logical)
    assert disk.stat().st_ino == inode
    assert d.container(disk) == (offset, logical)
    assert gpt_view(disk, offset, logical) == before  # every page reads as it did
    with open(disk, "rb") as f:
        assert f.read(16) != b"DroidVMDataStore"
        f.seek(34 * 512)
        assert f.read(offset - 34 * 512) == bytes(offset - 34 * 512)
    assert d.convert(disk, backup) == (offset, logical)  # again: the same disk


def test_a_converted_store_restores_as_it_was(tmp_path):
    logical = 512 << 20
    disk = tmp_path / "disk.raw"
    offset = store(disk, logical, {0: b"NXSB".ljust(PAGE, b"x"), 77: b"seventy-seven".ljust(PAGE, b"7")})
    size = disk.stat().st_size
    head = disk.read_bytes()[:offset]
    before = store_view(disk, offset, logical)
    backup = tmp_path / "backup"
    d.convert(disk, backup)
    assert d.restore(disk, backup)
    assert disk.stat().st_size == size and disk.read_bytes()[:offset] == head
    assert d.container(disk) is None and store_view(disk, offset, logical) == before
    d.discard_backup(backup)
    assert not d.restore(disk, backup)  # nothing saved: nothing to put back


def test_an_interrupted_conversion_completes(tmp_path, monkeypatch):
    logical = 512 << 20
    disk = tmp_path / "disk.raw"
    offset = store(disk, logical, {1: b"one".ljust(PAGE, b"1")})
    backup = tmp_path / "backup"
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
        d.convert(disk, backup)
    monkeypatch.setattr(os, "pwrite", real)
    assert json.loads((backup / "store.json").read_text())["offset"] == offset
    assert d.convert(disk, backup) == (offset, logical)
    assert d.container(disk) == (offset, logical)
    with open(disk, "rb") as f:
        f.seek(offset + PAGE)
        assert f.read(3) == b"one"
    # A half-written header is no obstacle to putting the store back either.
    assert d.restore(disk, backup) and d.container(disk) is None


def test_pages_written_after_the_conversion_survive_a_restore(tmp_path):
    """The upgrade's helper VM writes through the GPT disk, also where the store had no page; when the
    upgrade fails after that, the restored store must read those pages, and a second conversion keep them."""
    logical = 512 << 20
    disk = tmp_path / "disk.raw"
    offset = store(disk, logical, {0: b"NXSB".ljust(PAGE, b"x"), 9: b"nine".ljust(PAGE, b"9")})
    backup = tmp_path / "backup"
    d.convert(disk, backup)
    written = {9: b"rewritten".ljust(PAGE, b"r"), 5000: b"new".ljust(PAGE, b"n"),
               **{p: bytes([p % 251]) * PAGE for p in range(40000, 40021)}}  # a run across bitmap bytes
    with open(disk, "r+b") as f:
        for page, data in written.items():
            f.seek(offset + page * PAGE)
            f.write(data)
    after = gpt_view(disk, offset, logical)
    assert d.restore(disk, backup)
    assert d.container(disk) is None and store_view(disk, offset, logical) == after
    assert not (backup / "store.json").exists()  # spent: the next attempt saves the store afresh
    assert d.convert(disk, backup) == (offset, logical)
    assert gpt_view(disk, offset, logical) == after


def test_a_conversion_cut_while_punching_holes_resumes(tmp_path, monkeypatch):
    logical = 512 << 20
    disk = tmp_path / "disk.raw"
    offset = store(disk, logical, {1: b"one".ljust(PAGE, b"1")})
    with open(disk, "r+b") as f:  # two pages dropped from the bitmap, data left behind
        for page in (7, 3000):
            f.seek(offset + page * PAGE)
            f.write(b"stale".ljust(PAGE, b"s"))
    before = store_view(disk, offset, logical)
    head = disk.read_bytes()[:offset]
    real = d._punch
    calls = []

    def cut(fd, at, length):
        calls.append(at)
        if len(calls) == 2:
            raise OSError("power lost")
        return real(fd, at, length)

    backup = tmp_path / "backup"
    monkeypatch.setattr(d, "_punch", cut)
    with pytest.raises(OSError):
        d.convert(disk, backup)
    monkeypatch.setattr(d, "_punch", real)
    assert disk.read_bytes()[:offset] == head  # nothing but holes written yet
    assert d.convert(disk, backup) == (offset, logical)
    assert gpt_view(disk, offset, logical) == before
    # Put back before the holes were done, the store is left as it was.
    disk2 = tmp_path / "disk2.raw"
    offset = store(disk2, logical, {1: b"one".ljust(PAGE, b"1")})
    head = disk2.read_bytes()
    calls.clear()
    monkeypatch.setattr(d, "_punch", cut)
    with open(disk2, "r+b") as f:
        for page in (7, 3000):
            f.seek(offset + page * PAGE)
            f.write(b"stale".ljust(PAGE, b"s"))
    head = disk2.read_bytes()[:offset]
    with pytest.raises(OSError):
        d.convert(disk2, tmp_path / "backup2")
    monkeypatch.setattr(d, "_punch", real)
    assert d.restore(disk2, tmp_path / "backup2")
    assert disk2.read_bytes()[:offset] == head and d.container(disk2) is None


def test_bytes_after_the_container_are_put_back(tmp_path):
    logical = 512 << 20
    disk = tmp_path / "disk.raw"
    store(disk, logical, {2: b"two".ljust(PAGE, b"2")})
    with open(disk, "ab") as f:
        f.write(b"tail" * 1000)
    original = hashlib.sha256(disk.read_bytes()).hexdigest()
    backup = tmp_path / "backup"
    d.convert(disk, backup)
    assert d.restore(disk, backup)
    assert hashlib.sha256(disk.read_bytes()).hexdigest() == original
    with open(disk, "ab") as f:  # more than a store ever holds after its container
        f.truncate(disk.stat().st_size + (2 << 20))
    with pytest.raises(d.Invalid):
        d.convert(disk, tmp_path / "backup2")


def test_unknown_disks_are_refused(tmp_path):
    disk = tmp_path / "disk.raw"
    disk.write_bytes(bytes(3 * PAGE))
    with pytest.raises(d.Invalid):
        d.convert(disk, tmp_path / "j.json")
