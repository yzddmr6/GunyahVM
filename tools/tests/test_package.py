# SPDX-License-Identifier: MIT
# Copyright (C) 2026 yzddMr6
"""Standalone package payload, identity and source-build route integration tests."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.inputs


@pytest.fixture(scope="module")
def package(tmp_path_factory, input_cache):
    root = tmp_path_factory.mktemp("package-roundtrip")
    output = root / "out"
    subprocess.run(
        [
            sys.executable,
            str(REPO / "tools/build-virtualization-debs.py"),
            "--input-cache",
            str(input_cache),
            "--offline",
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    deb = output / "gunyahvm_1.0.0_arm64.deb"
    restored = root / "restored"
    subprocess.run(["dpkg-deb", "-x", str(deb), str(restored)], check=True)
    return deb, restored, output


def test_crosvm_and_windows_assets_survive_roundtrip(package, verified_inputs):
    _, restored, _ = package
    assert (restored / "usr/lib/gunyahvm/crosvm/crosvm").read_bytes() == (
        verified_inputs / "crosvm"
    ).read_bytes()
    for part, target in [
        ("windows", "usr/share/gunyahvm/windows"),
        ("licenses", "usr/share/doc/gunyahvm/licenses"),
    ]:
        source = verified_inputs / part
        expected = {
            str(p.relative_to(source)): p.read_bytes()
            for p in source.rglob("*")
            if p.is_file()
        }
        actual = {
            str(p.relative_to(restored / target)): p.read_bytes()
            for p in (restored / target).rglob("*")
            if p.is_file()
        }
        assert actual == expected


def test_package_identity_dependencies_and_manifest(package):
    deb, restored, output = package
    fields = subprocess.check_output(["dpkg-deb", "-f", str(deb)], text=True)
    assert "Package: gunyahvm\n" in fields and "Version: 1.0.0\n" in fields
    assert "Maintainer: yzddMr6 <yzddmr6@gmail.com>\n" in fields
    depends = next(x for x in fields.splitlines() if x.startswith("Depends:"))
    for name in [
        "remmina",
        "remmina-plugin-vnc",
        "wimtools",
        "7zip",
        "ntfs-3g",
        "python3-hivex",
        "dosfstools",
        "mtools",
    ]:
        assert name in depends.split(": ", 1)[1].split(", ")
    assert "Conflicts:" not in fields and "Replaces:" not in fields
    paths = [str(p.relative_to(restored)) for p in restored.rglob("*")]
    assert not any("piano" in p.lower() for p in paths)
    manifest = json.loads((output / "manifest.json").read_text())
    actual = {
        str(p.relative_to(restored)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in restored.rglob("*")
        if p.is_file()
    }
    assert actual == manifest
    assert all(not (p.stat().st_mode & 0o022) for p in restored.rglob("*"))
    for name in (
        "usr/bin/gunyahvm",
        "usr/lib/gunyahvm/crosvm/crosvm",
        "usr/libexec/gunyahvm/gunyahvm-run",
    ):
        assert (restored / name).stat().st_mode & 0o777 == 0o755
    assert (restored / "usr/share/gunyahvm/crosvm/empty").is_dir()
    assert not list((restored / "usr/share/gunyahvm/devices").iterdir())


def test_build_tree_and_release_inputs_preserve_identical_payload(
    package, tmp_path, verified_inputs
):
    # Build-tree layout uses the same trusted binaries, without compiling them.
    import shutil

    crosvm = tmp_path / "crosvm-build"
    windows = tmp_path / "windows-build"
    crosvm.mkdir()
    shutil.copyfile(verified_inputs / "crosvm", crosvm / "crosvm")
    source = crosvm / "src"
    source.mkdir()
    for file in (REPO / "assets/licenses/crosvm").iterdir():
        if file.is_file() and file.suffix != ".license":
            shutil.copyfile(file, source / file.name)
    (source / "third_party/minijail").mkdir(parents=True)
    shutil.copyfile(
        REPO / "assets/licenses/minijail/LICENSE",
        source / "third_party/minijail/LICENSE",
    )
    registry = crosvm / "cargo-home/registry/src/registry"
    registry.mkdir(parents=True)
    for crate in (REPO / "assets/licenses").iterdir():
        if crate.name not in ("crosvm", "minijail"):
            shutil.copytree(
                crate, registry / crate.name, ignore=shutil.ignore_patterns("*.license")
            )
    shutil.copytree(
        verified_inputs / "windows",
        windows / "assets",
        ignore=shutil.ignore_patterns("*.license"),
    )
    output = tmp_path / "rebuilt-package"
    subprocess.run(
        [
            sys.executable,
            str(REPO / "tools/build-virtualization-debs.py"),
            "--crosvm-build",
            str(crosvm),
            "--windows-build",
            str(windows),
            "--output",
            str(output),
        ],
        check=True,
        capture_output=True,
    )
    _, _, original = package
    assert json.loads((output / "manifest.json").read_text()) == json.loads(
        (original / "manifest.json").read_text()
    )
