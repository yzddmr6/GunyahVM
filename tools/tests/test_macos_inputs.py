# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Member-by-member official downloads: range, resume, cancel, checks."""
import hashlib
import io
import struct
import zlib

import pytest
from gunyahvm import macos_inputs as m

URL = "https://updates.cdn-apple.com/fixture.ipsw"


def archive(content, method):
    """A one-member ZIP-like body: local header at 0, then the member data."""
    data = zlib.compress(content)[2:-4] if method == 8 else content
    name = b"member.bin"
    header = b"PK\x03\x04" + struct.pack("<HHHHHIIIHH", 20, 0, method, 0, 0, zlib.crc32(content),
                                          len(data), len(content), len(name), 0) + name
    return header + data, data


class Response(io.BytesIO):
    status = 206

    def __init__(self, data, begin, end, size):
        super().__init__(data)
        self.headers = {"Content-Range": f"bytes {begin}-{end}/{size}"}

    def geturl(self):
        return URL

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def spec(monkeypatch, content, method=0):
    body, data = archive(content, method)
    item = {"ipsw": URL, "member": "member.bin", "offset": 0, "method": method,
            "compressed": len(data), "size": len(content), "crc32": zlib.crc32(content),
            "sha256": hashlib.sha256(content).hexdigest()}
    monkeypatch.setitem(m.IPSW_SIZE, URL, len(body))
    monkeypatch.setitem(m.MEMBERS, "fixture", item)
    return body


def opener_for(body, calls=None, fail_at=None):
    def get(request, timeout):
        begin, end = map(int, request.headers["Range"][6:].split("-"))
        if calls is not None:
            calls.append(begin)
            if fail_at is not None and len(calls) == fail_at:
                raise ConnectionError("network dropped")
        return Response(body[begin:end + 1], begin, end, len(body))
    return get


def test_stored_member_resumes_after_cancel(tmp_path, monkeypatch):
    content = bytes(range(256)) * 64
    body = spec(monkeypatch, content)
    monkeypatch.setattr(m, "CHUNK", 1024)
    seen = []
    with pytest.raises(m.Cancelled):
        m.fetch("fixture", tmp_path, progress=lambda d, t: seen.append(d),
                cancel=lambda: len(seen) >= 3, opener=opener_for(body))
    assert (tmp_path / "fixture.part").stat().st_size == 3072
    assert not (tmp_path / "fixture").exists()
    calls = []
    result = m.fetch("fixture", tmp_path, opener=opener_for(body, calls))
    assert result.read_bytes() == content
    assert calls == [30 + len("member.bin") + 3072]  # resumed, not restarted
    assert not (tmp_path / "fixture.part").exists()
    assert not (tmp_path / "fixture.part.json").exists()


def test_deflated_member_is_inflated_and_verified(tmp_path, monkeypatch):
    content = b"not Apple software " * 5000
    body = spec(monkeypatch, content, method=8)
    assert m.fetch("fixture", tmp_path, opener=opener_for(body)).read_bytes() == content


def test_network_errors_retry_from_the_current_offset(tmp_path, monkeypatch):
    content = b"x" * 5000
    body = spec(monkeypatch, content)
    calls = []
    result = m.fetch("fixture", tmp_path, opener=opener_for(body, calls, fail_at=2),
                     sleep=lambda s: None)
    assert result.read_bytes() == content


def test_checksum_failure_never_publishes(tmp_path, monkeypatch):
    content = b"y" * 4096
    body = spec(monkeypatch, content)
    m.MEMBERS["fixture"]["crc32"] ^= 1
    with pytest.raises(ValueError, match="checksum"):
        m.fetch("fixture", tmp_path, opener=opener_for(body))
    assert not (tmp_path / "fixture").exists()
    assert not (tmp_path / "fixture.part").exists()


def test_changed_server_layout_is_refused(tmp_path, monkeypatch):
    body = spec(monkeypatch, b"z" * 100)
    with pytest.raises(ValueError, match="pinned layout"):
        m.fetch("fixture", tmp_path, opener=opener_for(b"XX" + body[2:]))


def test_symlink_partial_is_not_followed(tmp_path, monkeypatch):
    body = spec(monkeypatch, b"w" * 100)
    victim = tmp_path / "disk.raw"
    victim.write_bytes(b"owner")
    (tmp_path / "fixture.part").symlink_to(victim)
    with pytest.raises(OSError):
        m.fetch("fixture", tmp_path, opener=opener_for(body))
    assert victim.read_bytes() == b"owner"


@pytest.mark.parametrize("url", ["http://apple.com/a", "https://apple.com.evil.test/a",
                                 "https://user@apple.com/a", "https://example.com/a"])
def test_external_redirect_rejected_before_following(url):
    with pytest.raises(ValueError, match="directly from Apple"):
        m.AppleRedirect().redirect_request(None, None, 302, "", {}, url)


def test_pins_name_only_the_needed_members():
    assert 13_200_000_000 < m.total_download() < 13_400_000_000
    for item in m.MEMBERS.values():
        assert item["ipsw"] in m.IPSW_SIZE and item["method"] in (0, 8)
        assert item["offset"] + item["compressed"] < m.IPSW_SIZE[item["ipsw"]]


def test_clean_only_owned_partial_names(tmp_path):
    (tmp_path / "os.aea.part").write_bytes(b"partial")
    (tmp_path / "os.aea.part.json").write_text("{}")
    unrelated = tmp_path / "disk.raw"
    unrelated.write_bytes(b"owner")
    m.clean_partials(tmp_path)
    assert not (tmp_path / "os.aea.part").exists()
    assert unrelated.read_bytes() == b"owner"


def test_cli_cancel_exits_without_traceback(monkeypatch, tmp_path, capsys):
    from gunyahvm import cli
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)

    def cancel(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(m, "fetch", cancel)
    assert cli.main(["--store", str(tmp_path), "fetch-macos-inputs"]) == 130
    assert "resume" in capsys.readouterr().err
