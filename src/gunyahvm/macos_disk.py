# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The macOS system disk: one APFS container in the only partition of a GPT disk.

The guest reads disk.raw through Apple's own virtio block driver on crosvm's
PCI host, so the file is a plain disk, laid out as macOS-DroidVM lays out its
PCIe system disk (research/pcie-data-2026-11-11/prepare-gpt.py): a protective
MBR, the GPT header and 128 entries at the start, their backup at the end,
and the container in partition 1.

GunyahVM 1.3 to 1.5 kept the same container in a DroidVMDataStore instead
(block 0 its label, block 1 NativeRamBlock's SHNGDATA overlay header, then a
presence bitmap with one bit per page, then the container), which the board's
NativeRamBlock served over virtio-mmio. convert() turns such a store into the
GPT disk where it lies: the container does not move, only the blocks before
it and a trailer after it are written, and pages the bitmap marks absent are
returned to holes, so the disk reads as the store did.
"""

import json
import os
from pathlib import Path
import struct
import uuid
import zlib

PAGE = 4096
SECTOR = 512
ENTRIES = 128
ENTRY_SIZE = 128
TABLE_SECTORS = ENTRIES * ENTRY_SIZE // SECTOR  # 32
FIRST_USABLE = 2 + TABLE_SECTORS                # 34
NEW_START = 2048                                # a new disk's container, at 1 MiB
TRAILER = 1 << 20                               # after the container: room for the backup GPT
# The partition type and name of macOS-DroidVM's converted disks.
TYPE = uuid.UUID("69646961-6700-11AA-AA11-00306543ECAC")
NAME = "DroidVM Container"
LABEL = b"DroidVMDataStore"
OVERLAY = b"SHNGDATA"


class Invalid(ValueError):
    pass


def _entries(start, last, guid):
    table = bytearray(ENTRIES * ENTRY_SIZE)
    table[:16] = TYPE.bytes_le
    table[16:32] = guid.bytes_le
    struct.pack_into("<QQQ", table, 32, start, last, 0)
    name = NAME.encode("utf-16le")
    table[56:56 + len(name)] = name
    return bytes(table)


def _header(at, other, table_lba, total, disk_guid, table):
    data = bytearray(SECTOR)
    struct.pack_into("<8sIIIIQQQQ16sQIII", data, 0, b"EFI PART", 0x10000, 92, 0, 0, at, other,
                     FIRST_USABLE, total - FIRST_USABLE, disk_guid.bytes_le, table_lba, ENTRIES,
                     ENTRY_SIZE, zlib.crc32(table))
    struct.pack_into("<I", data, 16, zlib.crc32(data[:92]))
    return bytes(data)


def gpt(total, start, logical, disk_guid=None, part_guid=None):
    """(primary: the first 34 sectors, backup: the last 33) for a container of `logical`
    bytes at sector `start` of a disk of `total` sectors."""
    if start < FIRST_USABLE or logical % PAGE or start + logical // SECTOR > total - 1 - TABLE_SECTORS:
        raise Invalid("the container does not fit the GPT disk")
    disk_guid, part_guid = disk_guid or uuid.uuid4(), part_guid or uuid.uuid4()
    table = _entries(start, start + logical // SECTOR - 1, part_guid)
    mbr = bytearray(SECTOR)
    mbr[446:462] = struct.pack("<B3sB3sII", 0, b"\0\2\0", 0xEE, b"\xff" * 3, 1, min(total - 1, 0xFFFFFFFF))
    mbr[510:512] = b"\x55\xaa"
    primary = bytes(mbr) + _header(1, total - 1, 2, total, disk_guid, table) + table
    backup = table + _header(total - 1, 1, total - 1 - TABLE_SECTORS, total, disk_guid, table)
    return primary, backup


def container(path):
    """(offset, logical) of the container of a GPT disk.raw, or None if it is not one."""
    with open(path, "rb") as stream:
        head = stream.read(FIRST_USABLE * SECTOR)
    if len(head) < FIRST_USABLE * SECTOR or head[SECTOR:SECTOR + 8] != b"EFI PART":
        return None
    header = bytearray(head[SECTOR:SECTOR + 92])
    expected, = struct.unpack_from("<I", header, 16)
    struct.pack_into("<I", header, 16, 0)
    table = head[2 * SECTOR:FIRST_USABLE * SECTOR]
    if zlib.crc32(header) != expected or zlib.crc32(table) != struct.unpack_from("<I", head, SECTOR + 88)[0]:
        raise Invalid(f"{path}: the GPT header is damaged")
    if uuid.UUID(bytes_le=table[:16]) != TYPE:
        raise Invalid(f"{path}: partition 1 is not the macOS container")
    first, last = struct.unpack_from("<QQ", table, 32)
    return first * SECTOR, (last + 1 - first) * SECTOR


def create(path, logical):
    """A new sparse GPT disk.raw for a container of `logical` bytes; returns its offset."""
    if logical % PAGE:
        raise Invalid("the container size must be a whole number of pages")
    total = (NEW_START * SECTOR + logical + TRAILER) // SECTOR
    primary, backup = gpt(total, NEW_START, logical)
    with open(path, "xb") as handle:
        handle.truncate(total * SECTOR)
        handle.write(primary)
        handle.seek((total - 1 - TABLE_SECTORS) * SECTOR)
        handle.write(backup)
        os.fsync(handle.fileno())
    return NEW_START * SECTOR


def store_geometry(head):
    """(offset, logical, bitmap pages) of a DroidVMDataStore from its first two blocks, or None."""
    if head[:len(LABEL)] != LABEL:
        return None
    overlay = head[PAGE:2 * PAGE]
    version, size, pages = struct.unpack_from("<IIQ", overlay, 8)
    if overlay[:8] != OVERLAY or (version, size) != (1, 64) or not pages:
        raise Invalid("not a SHNGDATA overlay of version 1")
    bitmap = (pages + 8 * PAGE - 1) // (8 * PAGE)
    return (2 + bitmap) * PAGE, pages * PAGE, bitmap


def _absent_to_holes(fd, offset, logical, bitmap):
    """Punch out the data of pages whose bit is clear: the store read them as zero."""
    end = offset + logical
    at = offset
    while at < end:
        try:
            start = os.lseek(fd, at, os.SEEK_DATA)
        except OSError:
            return
        if start >= end:
            return
        stop = min(os.lseek(fd, start, os.SEEK_HOLE), end)
        first, limit = (start - offset) // PAGE, (stop - offset + PAGE - 1) // PAGE
        index = first
        while index < limit:
            if bitmap[index // 8] >> (index % 8) & 1:
                index += 1
                continue
            run = index
            while index < limit and not bitmap[index // 8] >> (index % 8) & 1:
                index += 1
            _punch(fd, offset + run * PAGE, (index - run) * PAGE)
        at = stop


def _punch(fd, offset, length):
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    libc.fallocate.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_long, ctypes.c_long]
    # FALLOC_FL_PUNCH_HOLE | FALLOC_FL_KEEP_SIZE
    if libc.fallocate(fd, 0x02 | 0x01, offset, length):
        raise OSError(ctypes.get_errno(), "cannot punch a hole in the macOS disk")


def convert(path, journal):
    """Turn a DroidVMDataStore into a GPT disk in place; returns (offset, logical).

    `journal` records the geometry before the store's own header is
    overwritten, so an interrupted conversion completes on the next call.
    A disk that already is a GPT disk is left as it is.
    """
    path, journal = Path(path), Path(journal)
    done = container(path) if not journal.exists() else None
    if done:
        return done
    with open(path, "r+b") as handle:
        fd = handle.fileno()
        if journal.exists():
            record = json.loads(journal.read_text())
            offset, logical = record["offset"], record["logical"]
        else:
            head = handle.read(2 * PAGE)
            geometry = store_geometry(head)
            if not geometry:
                raise Invalid(f"{path} is neither a GPT disk nor a DroidVMDataStore")
            offset, logical, bitmap_pages = geometry
            if os.fstat(fd).st_size < offset + logical:
                raise Invalid(f"{path} is shorter than its store says")
            handle.seek(2 * PAGE)
            bitmap = handle.read(bitmap_pages * PAGE)
            _absent_to_holes(fd, offset, logical, bitmap)
            os.fsync(fd)
            partial = journal.with_name(journal.name + ".part")
            partial.write_text(json.dumps({"offset": offset, "logical": logical}))
            os.replace(partial, journal)
        total = (offset + logical + TRAILER) // SECTOR
        primary, backup = gpt(total, offset // SECTOR, logical)
        handle.truncate(total * SECTOR)
        os.pwrite(fd, backup, (total - 1 - TABLE_SECTORS) * SECTOR)
        os.fsync(fd)
        os.pwrite(fd, primary, 0)
        # The rest of the store's blocks before the container held its header and bitmap.
        os.pwrite(fd, bytes(offset - len(primary)), len(primary))
        os.fsync(fd)
    journal.unlink()
    return offset, logical
