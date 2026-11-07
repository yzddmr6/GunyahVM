# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Offline trust-boundary tests for fetched and cached release inputs."""

import hashlib
import io
import subprocess
import tarfile
import zipfile

import pytest

from prebuilt_inputs import fetch, unpack_release, unpack_drivers


def digest(data):
    return hashlib.sha256(data).hexdigest()


def test_download_and_offline_cache_are_both_checked(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"locked payload")
    item = {
        "filename": "input",
        "url": source.as_uri(),
        "sha256": digest(source.read_bytes()),
    }
    cache = tmp_path / "cache"
    result = fetch(item, cache)
    assert result.read_bytes() == source.read_bytes()
    source.unlink()
    assert fetch(item, cache, offline=True) == result
    result.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="Cached input checksum mismatch"):
        fetch(item, cache, offline=True)


def test_failed_download_never_enters_cache(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"bad download")
    item = {"filename": "input", "url": source.as_uri(), "sha256": digest(b"expected")}
    with pytest.raises(ValueError, match="Downloaded input checksum mismatch"):
        fetch(item, tmp_path / "cache")
    assert not list((tmp_path / "cache").iterdir())
    with pytest.raises(ValueError, match="Missing offline input"):
        fetch(item, tmp_path / "cache", offline=True)


def make_archive(tmp_path, names, kind=tarfile.REGTYPE):
    raw = tmp_path / "input.tar"
    with tarfile.open(raw, "w") as archive:
        for name in names:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.linkname = "/outside"
            member.size = 7 if kind == tarfile.REGTYPE else 0
            archive.addfile(member, io.BytesIO(b"payload") if member.size else None)
    compressed = tmp_path / "input.tar.zst"
    subprocess.run(["zstd", "-q", "-o", str(compressed), str(raw)], check=True)
    spec = {
        "sha256": digest(compressed.read_bytes()),
        "files": {"crosvm": {"sha256": digest(b"payload"), "size": 7}},
    }
    return compressed, spec


def test_release_exact_inventory(tmp_path):
    archive, spec = make_archive(tmp_path, ["crosvm"])
    unpack_release(archive, tmp_path / "out", spec)
    assert (tmp_path / "out/crosvm").read_bytes() == b"payload"


@pytest.mark.parametrize(
    "names,kind",
    [
        (["../escape"], tarfile.REGTYPE),
        (["/absolute"], tarfile.REGTYPE),
        (["crosvm", "crosvm"], tarfile.REGTYPE),
        (["crosvm", "extra"], tarfile.REGTYPE),
        ([], tarfile.REGTYPE),
        (["crosvm"], tarfile.SYMTYPE),
        (["crosvm"], tarfile.LNKTYPE),
    ],
)
def test_release_rejects_unsafe_or_incomplete_inventory(tmp_path, names, kind):
    archive, spec = make_archive(tmp_path, names, kind)
    with pytest.raises(ValueError):
        unpack_release(archive, tmp_path / "out", spec)
    assert not (tmp_path / "escape").exists()


@pytest.mark.parametrize("field,value", [("sha256", "0" * 64), ("size", 8)])
def test_release_checks_member_size_and_hash(tmp_path, field, value):
    archive, spec = make_archive(tmp_path, ["crosvm"])
    spec["files"]["crosvm"][field] = value
    with pytest.raises(ValueError):
        unpack_release(archive, tmp_path / "out", spec)


def test_archive_hash_checked_before_extraction(tmp_path):
    archive, spec = make_archive(tmp_path, ["crosvm"])
    spec["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Release input checksum mismatch"):
        unpack_release(archive, tmp_path / "out", spec)
    assert not (tmp_path / "out").exists()


def test_only_selected_original_driver_bytes_are_extracted(tmp_path):
    archive = tmp_path / "drivers.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("drivers/test.sys", b"signed driver")
        z.writestr("unused.exe", b"excluded")
        z.writestr("../escape", b"excluded")
    spec = {"sha256": digest(archive.read_bytes())}
    inventory = {"drivers/test.sys": {"sha256": digest(b"signed driver")}}
    unpack_drivers(archive, tmp_path / "out", spec, inventory)
    assert (tmp_path / "out/drivers/test.sys").read_bytes() == b"signed driver"
    assert not (tmp_path / "out/unused.exe").exists()
    assert not (tmp_path / "escape").exists()
    inventory["drivers/test.sys"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="Driver checksum mismatch"):
        unpack_drivers(archive, tmp_path / "bad", spec, inventory)


def test_release_lock_uses_own_release_and_upstream_driver_origin():
    import json
    from prebuilt_inputs import REPO

    item = json.loads((REPO / "tools/prebuilt.lock.json").read_text())["release"]
    assert (
        item["url"]
        == "https://github.com/yzddmr6/GunyahVM/releases/download/v1.0.0/"
        + item["filename"]
    )
    assert item["filename"] == "gunyahvm-1.0.0-inputs.tar.zst"
    assert {name for name in item["files"] if not name.startswith("notices/")} == {
        "crosvm",
        "windows/firmware.fd",
        "windows/winpe-helper.exe",
    }
    assert "notices/NOTICE" in item["files"] and "notices/LICENSE" in item["files"]
    drivers = json.loads((REPO / "tools/windows.lock.json").read_text())["drivers"]
    assert drivers["url"].startswith(
        "https://github.com/Droid-VM/gunyah-guest-drivers-windows/releases/"
    )
    assert drivers["asset_url"].endswith("/558946077")
