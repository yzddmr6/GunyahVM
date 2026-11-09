# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Optional, cached release checks; no host or device facts enter the request."""

import http.client
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.request

from . import __version__
from .storage import atomic_json

LATEST = "https://api.github.com/repos/yzddmr6/GunyahVM/releases/latest"
RELEASE_PREFIX = "https://github.com/yzddmr6/GunyahVM/releases/tag/"
TTL = 24 * 60 * 60


def preferences_path():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "gunyahvm/preferences.json"


def cache_path():
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "gunyahvm/update.json"


def read_json(path):
    try:
        with path.open() as stream:
            value = json.loads(stream.read(256 * 1024))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def enabled():
    return (
        os.environ.get("GUNYAHVM_NO_UPDATE_CHECK") != "1"
        and read_json(preferences_path()).get("check_updates", True) is not False
    )


def set_enabled(value):
    path = preferences_path()
    settings = read_json(path)
    settings["check_updates"] = bool(value)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_json(path, settings)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def fetch_latest():
    request = urllib.request.Request(
        LATEST, headers={"Accept": "application/vnd.github+json", "User-Agent": "GunyahVM"}
    )
    # Do not follow redirects or retry: at most one HTTP request per check.
    opener = urllib.request.build_opener(NoRedirect())
    with opener.open(request, timeout=5) as response:
        data = json.loads(response.read(256 * 1024))
    if data.get("draft") or data.get("prerelease"):
        return None
    return {"version": data.get("tag_name"), "url": data.get("html_url")}


def newer_release(release, current):
    if not isinstance(release, dict):
        return None
    version, url = release.get("version"), release.get("url")
    if not isinstance(version, str) or not isinstance(url, str):
        return None
    version = version.removeprefix("v")
    if not version or not version[0].isdigit() or not url.startswith(RELEASE_PREFIX):
        return None
    result = subprocess.run(
        ["dpkg", "--compare-versions", version, "gt", current],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        timeout=2, check=False,
    )
    return {"version": version, "url": url} if result.returncode == 0 else None


def check(current=__version__):
    """Run in a worker thread. All network/cache/compare failures are silent."""
    if not enabled():
        return None
    now = time.time()
    path = cache_path()
    cached = read_json(path)
    stamp = cached.get("checked_at")
    fresh = isinstance(stamp, (int, float)) and 0 <= now - stamp < TTL
    release = cached.get("release") if fresh else None
    if not fresh:
        try:
            release = fetch_latest()
        except (OSError, ValueError, TypeError, AttributeError, http.client.HTTPException):
            release = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            atomic_json(path, {"checked_at": now, "release": release})
        except OSError:
            pass
    try:
        return newer_release(release, current) if enabled() else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None
