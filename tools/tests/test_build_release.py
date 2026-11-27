# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Release inputs shared by worktrees keep distinct locked revisions."""

import hashlib
import importlib.util
from pathlib import Path


def test_cache_keeps_two_revisions_of_the_same_filename(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "build_release", Path(__file__).parents[1] / "build-release.py"
    )
    release = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(release)
    source = tmp_path / "upstream"
    cache = tmp_path / "shared"
    paths = []
    for data in (b"first revision", b"second revision"):
        source.write_bytes(data)
        item = {"filename": "input.tar", "url": source.as_uri(),
                "sha256": hashlib.sha256(data).hexdigest()}
        paths.append(release.cached(item, cache))
    source.unlink()
    assert [p.read_bytes() for p in paths] == [b"first revision", b"second revision"]
    assert release.cached(item, cache) == paths[1]
