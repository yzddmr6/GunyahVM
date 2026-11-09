# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Offline release notifications, Debian ordering and privacy controls."""

import io
import json
import urllib.error

import pytest
from gunyahvm import updates


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.delenv("GUNYAHVM_NO_UPDATE_CHECK", raising=False)
    monkeypatch.setattr(updates.time, "time", lambda: 100000)


def release(version="v1.0.1"):
    return {"version": version, "url": updates.RELEASE_PREFIX + version}


def test_available_update_and_24_hour_cache(isolated, monkeypatch):
    calls = []
    monkeypatch.setattr(updates, "fetch_latest", lambda: calls.append(1) or release())
    assert updates.check("1.0.0") == {**release(), "version": "1.0.1"}
    assert updates.check("1.0.0")["version"] == "1.0.1"
    assert calls == [1]
    monkeypatch.setattr(updates.time, "time", lambda: 100000 + updates.TTL)
    assert updates.check("1.0.0")["version"] == "1.0.1"
    assert calls == [1, 1]


@pytest.mark.parametrize("version", ["v1.0.0", "v0.9.9", "v1.0.0~rc1"])
def test_no_update(isolated, monkeypatch, version):
    calls = []
    monkeypatch.setattr(updates, "fetch_latest", lambda: calls.append(1) or release(version))
    assert updates.check("1.0.0") is None
    assert updates.check("1.0.0") is None
    assert calls == [1]


@pytest.mark.parametrize("error", [TimeoutError(), urllib.error.URLError("offline"), ValueError("JSON")])
def test_failure_is_silent_and_cached(isolated, monkeypatch, error):
    calls = []

    def fail():
        calls.append(1)
        raise error

    monkeypatch.setattr(updates, "fetch_latest", fail)
    assert updates.check() is None
    assert updates.check() is None
    assert calls == [1]


@pytest.mark.parametrize("method", ["settings", "environment"])
def test_disabled_never_requests_or_displays_cached_update(isolated, monkeypatch, method):
    monkeypatch.setattr(updates, "fetch_latest", lambda: release())
    assert updates.check() is not None
    if method == "settings":
        updates.set_enabled(False)
    else:
        monkeypatch.setenv("GUNYAHVM_NO_UPDATE_CHECK", "1")
    monkeypatch.setattr(updates, "fetch_latest", lambda: pytest.fail("unexpected network request"))
    assert updates.check() is None
    updates.cache_path().unlink()
    assert updates.check() is None
    assert not updates.cache_path().exists()


@pytest.mark.parametrize(
    "new,current,expected",
    [("v1.0.10", "1.0.9", True), ("1:1.0", "9.0", True),
     ("1.0.0-2", "1.0.0-1", True), ("1.0.0~rc2", "1.0.0", False)],
)
def test_uses_debian_version_order(new, current, expected):
    assert bool(updates.newer_release(release(new), current)) is expected


def test_one_fixed_anonymous_request_with_five_second_timeout(monkeypatch):
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append((request.full_url, request.get_method(), request.data, request.header_items(), timeout))
            return io.BytesIO(json.dumps({"tag_name": "v1.0.1", "html_url": release()["url"]}).encode())

    def build(handler):
        assert isinstance(handler, updates.NoRedirect)
        assert handler.redirect_request(None, None, 302, "", {}, "https://example.invalid") is None
        return Opener()

    monkeypatch.setattr(updates.urllib.request, "build_opener", build)
    assert updates.fetch_latest() == release()
    assert calls == [(updates.LATEST, "GET", None,
                      [("Accept", "application/vnd.github+json"), ("User-agent", "GunyahVM")], 5)]


def test_release_link_must_belong_to_project():
    assert updates.newer_release({"version": "2.0", "url": "https://example.invalid"}, "1.0") is None
