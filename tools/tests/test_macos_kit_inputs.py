# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The macOS kit's source build: its pinned inputs and the workspace and mirror logic, offline."""
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("kit_inputs", REPO / "tools/build-macos-kit-inputs.py")
k = importlib.util.module_from_spec(spec)
spec.loader.exec_module(k)
LOCK = json.loads((REPO / "tools/macos.lock.json").read_text())
SOURCE = LOCK["kit"]["source"]


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def test_the_lock_pins_the_repository_files_the_build_reads():
    for patch in [*SOURCE["patches"], *SOURCE["xnu_patches"], *SOURCE["build_files"]]:
        assert digest(REPO / patch["file"]) == patch["sha256"], patch["file"]
    container = SOURCE["container"]
    assert digest(REPO / container["dockerfile"]) == container["dockerfile_sha256"]
    # The base image and the archive snapshot are fixed, so the packages are too.
    text = (REPO / container["dockerfile"]).read_text()
    assert re.search(r"^FROM ubuntu@sha256:[0-9a-f]{64}$", text, re.M)
    assert re.search(r"^ARG SNAPSHOT=\d{8}T\d{6}Z$", text, re.M)
    assert re.search(r"^ARG RUSTUP_SHA256=[0-9a-f]{64}$", text, re.M)


def test_the_workspace_patch_adds_exactly_our_xnu_patches_to_the_series():
    patch = (REPO / SOURCE["patches"][0]["file"]).read_text()
    added = re.findall(r'^\+\s+"(gunyahvm-[^"]+\.patch)",?$', patch, re.M)
    assert added == [Path(p["as"]).name for p in SOURCE["xnu_patches"]]
    assert all(p["as"].startswith("patches/xnu-12377/") for p in SOURCE["xnu_patches"])
    # The kernel links at the ARM_LARGE_MEMORY base, which the collection keeps (no shift).
    assert "ARM64_LARGE_MEMORY=1" in patch and "+KB=0xfffffe0007004000" in patch


def test_the_build_outputs_are_the_kit_files_it_does_not_take_as_given():
    built = set(SOURCE["outputs"])
    given = {"fdt"}  # crosvm's device tree of the profile, dumped on a device
    assert built | given == set(LOCK["kit"]["prebuilt"].values())
    assert not built & given
    assert LOCK["kit"]["kexts"] == LOCK["kit"]["kc"]["native"]
    assert "com.apple.filesystems.lifs" in LOCK["kit"]["kc"]["keep"]


@pytest.mark.parametrize("avail_kib,load,cpus,expected", [
    (25 << 20, 0.0, 12, 12),   # an idle 31 GiB host: every CPU
    (25 << 20, 9.6, 12, 4),    # another build already runs: at least 4
    (12 << 20, 0.0, 12, 4),    # little memory left: 1.5 GiB a job beyond a 6 GiB reserve
    (16 << 20, 1.0, 12, 6),
])
def test_jobs_follow_available_memory_and_idle_cpus(monkeypatch, tmp_path, avail_kib, load, cpus, expected):
    meminfo = tmp_path / "meminfo"
    meminfo.write_text(f"MemTotal: 32000000 kB\nMemAvailable: {avail_kib} kB\n")
    real_open = open
    monkeypatch.setattr(k, "open", lambda path, *a, **kw: real_open(meminfo if path == "/proc/meminfo" else path, *a, **kw),
                        raising=False)
    monkeypatch.setattr(k.os, "getloadavg", lambda: (load, load, load))
    monkeypatch.setattr(k.os, "cpu_count", lambda: cpus)
    assert k.jobs() == expected


def git(*args, cwd):
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@l", *args], cwd=cwd, check=True,
                   capture_output=True)


def fake_upstream(tmp_path):
    """A one-commit repository standing in for macOS-DroidVM, and a lock source for it."""
    upstream = tmp_path / "upstream"
    (upstream / "configs").mkdir(parents=True)
    (upstream / "patches/xnu-12377").mkdir(parents=True)
    (upstream / "configs/env.lock.json").write_text('{"x12377": {"series": ["00*.patch"]}}\n')
    (upstream / "patches/xnu-12377/README.md").write_text("series\n")
    git("init", "-q", cwd=upstream)
    git("add", "-A", cwd=upstream)
    git("commit", "-q", "-m", "base", cwd=upstream)
    commit = subprocess.check_output(["git", "-C", str(upstream), "rev-parse", "HEAD"], text=True).strip()
    (upstream / "configs/env.lock.json").write_text('{"x12377": {"series": ["00*.patch", "gunyahvm-x.patch"]}}\n')
    patch = subprocess.check_output(["git", "-C", str(upstream), "diff"], text=True)
    git("checkout", "-q", "--", ".", cwd=upstream)
    ours = tmp_path / "repo"
    (ours / "kit").mkdir(parents=True)
    (ours / "kit/workspace.patch").write_text(patch)
    (ours / "kit/x.patch").write_text("xnu change\n")
    lock_env = hashlib.sha256(b'{"x12377": {"series": ["00*.patch", "gunyahvm-x.patch"]}}\n').hexdigest()
    source = {"repository": str(upstream), "commit": commit,
              "patches": [{"file": "kit/workspace.patch", "sha256": digest(ours / "kit/workspace.patch")}],
              "xnu_patches": [{"file": "kit/x.patch", "as": "patches/xnu-12377/gunyahvm-x.patch",
                               "sha256": digest(ours / "kit/x.patch")}],
              "env_lock_sha256": lock_env, "build_files": []}
    return ours, source


def test_a_new_workspace_is_the_pinned_commit_with_our_patches(tmp_path, monkeypatch):
    ours, source = fake_upstream(tmp_path)
    monkeypatch.setattr(k, "REPO", ours)
    workspace = tmp_path / "ws"
    k.prepare(workspace, source)
    assert (workspace / "patches/xnu-12377/gunyahvm-x.patch").read_text() == "xnu change\n"
    k.prepare(workspace, source)  # a later run only checks
    (workspace / "patches/xnu-12377/gunyahvm-x.patch").write_text("edited\n")
    k.prepare(workspace, source)  # the patches it holds are ours again
    assert (workspace / "patches/xnu-12377/gunyahvm-x.patch").read_text() == "xnu change\n"
    # A workspace patched by an earlier version of the workspace patch takes the current one.
    (workspace / "configs/env.lock.json").write_text('{"x12377": {"series": ["00*.patch", "older.patch"]}}\n')
    k.prepare(workspace, source)
    assert "gunyahvm-x.patch" in (workspace / "configs/env.lock.json").read_text()
    other = tmp_path / "other"
    other.mkdir()
    (other / "file").write_text("x")
    with pytest.raises(SystemExit, match="not empty"):
        k.prepare(other, source)


def test_the_mirror_restores_what_a_new_workspace_lacks(tmp_path):
    source = {"commit": "c0ffee", "mirror": ["sources/Team", "toolchains/sdk.tar.xz"]}
    built = tmp_path / "built"
    (built / "sources/Team/.git").mkdir(parents=True)
    (built / "sources/Team/.git/HEAD").write_text("c0ffee\n")
    (built / "sources/Team/driver.c").write_text("int x;\n")
    (built / "toolchains").mkdir()
    (built / "toolchains/sdk.tar.xz").write_bytes(b"sdk")
    mirror = tmp_path / "mirror"
    k.save_mirror(built, source, mirror)
    index = json.loads((mirror / "mirror.json").read_text())
    assert index["commit"] == "c0ffee" and set(index["paths"]) == set(source["mirror"])
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    k.restore_mirror(fresh, source, mirror)
    assert (fresh / "sources/Team/driver.c").read_text() == "int x;\n"
    assert (fresh / "sources/Team/.git/HEAD").read_text() == "c0ffee\n"
    assert (fresh / "toolchains/sdk.tar.xz").read_bytes() == b"sdk"
    archive = mirror / index["paths"]["toolchains/sdk.tar.xz"]["file"]
    archive.write_bytes(archive.read_bytes() + b"x")
    again = tmp_path / "again"
    again.mkdir()
    with pytest.raises(SystemExit, match="checksum differs"):
        k.restore_mirror(again, source, mirror)
    assert not any(again.iterdir())  # nothing is restored from a mirror with a bad archive
    with pytest.raises(SystemExit, match="holds the sources of"):
        k.restore_mirror(fresh, {**source, "commit": "other"}, mirror)
