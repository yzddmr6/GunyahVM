# SPDX-License-Identifier: MIT
# Copyright (C) 2026 yzddMr6
"""Standalone package payload, identity and source-build route integration tests."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from gunyahvm import __version__

DEB_VERSION = __version__ + "-1"

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
    deb = output / f"gunyahvm_{DEB_VERSION}_arm64.deb"
    restored = root / "restored"
    for part in output.glob("*.deb"):
        subprocess.run(["dpkg-deb", "-x", str(part), str(restored)], check=True)
    return deb, restored, output


def test_crosvm_and_windows_assets_survive_roundtrip(package, verified_inputs):
    _, restored, _ = package
    runtime_lock = json.loads((REPO / "tools/runtime.lock.json").read_text())
    for relative, digest in runtime_lock["outputs"].items():
        assert hashlib.sha256((restored / relative).read_bytes()).hexdigest() == digest
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
    assert "Package: gunyahvm\n" in fields and f"Version: {DEB_VERSION}\n" in fields
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
    assert all(
        "usr/share/gunyahvm/devices/" in p for p in paths if "piano" in p.lower()
    )
    manifest = json.loads((output / "manifest.json").read_text())
    actual = {
        str(p.relative_to(restored)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in restored.rglob("*")
        if p.is_file()
    }
    assert actual == {
        path: digest for part in manifest.values() for path, digest in part.items()
    }
    assert all(not (p.stat().st_mode & 0o022) for p in restored.rglob("*"))
    for name in (
        "usr/bin/gunyahvm",
        "usr/lib/gunyahvm/crosvm/crosvm",
        "usr/libexec/gunyahvm/gunyahvm-helper",
    ):
        assert (restored / name).stat().st_mode & 0o777 == 0o755
    assert (restored / "usr/share/gunyahvm/crosvm/empty").is_dir()
    assert {p.name for p in (restored / "usr/share/gunyahvm/devices").iterdir()} == {
        "piano.toml",
        "erhai.toml",
    }
    assert (restored / "usr/share/gunyahvm/runtime.json").is_file()


def test_build_tree_and_release_inputs_preserve_identical_payload(
    package, tmp_path, verified_inputs, input_cache
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
            "--input-cache",
            str(input_cache),
            "--offline",
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


def test_runtime_packages_are_an_exact_default_dependency_set(package):
    deb, restored, output = package
    names = {p.name.split("_", 1)[0] for p in output.glob("*.deb")}
    assert names == {
        "gunyahvm",
        "gunyahvm-runtime",
        "gunyahvm-guest-kernel",
        "gunyahvm-guest-mesa",
    }
    fields = subprocess.check_output(["dpkg-deb", "-f", str(deb), "Depends"], text=True)
    assert f"gunyahvm-runtime (= {DEB_VERSION})" in fields
    runtime = output / f"gunyahvm-runtime_{DEB_VERSION}_arm64.deb"
    fields = subprocess.check_output(
        ["dpkg-deb", "-f", str(runtime), "Depends"], text=True
    )
    assert f"gunyahvm-guest-kernel (= {DEB_VERSION})" in fields
    assert f"gunyahvm-guest-mesa (= {DEB_VERSION})" in fields
    from gunyahvm import components

    manifest = components.load(restored / "usr/share/gunyahvm/runtime.json", restored)
    assert manifest["components"]["renderer"]["features"] == [
        "virgl",
        "venus",
        "drm-msm",
    ]
    ownership = json.loads((output / "manifest.json").read_text())
    paths = [path for part in ownership.values() for path in part]
    assert len(paths) == len(set(paths)), (
        "payload files must have exactly one package owner"
    )


def test_desktop_launcher_and_matching_icon_are_installed(package):
    import configparser

    _, restored, _ = package
    desktop = restored / "usr/share/applications/io.github.yzddmr6.GunyahVM.desktop"
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(desktop)
    entry = parser["Desktop Entry"]
    assert entry["Exec"] == "gunyahvm gui"
    assert entry["Type"] == "Application"
    assert not entry.getboolean("Hidden", False)
    assert not entry.getboolean("NoDisplay", False)
    icon = restored / "usr/share/icons/hicolor/scalable/apps" / (entry["Icon"] + ".svg")
    assert icon.is_file()
    assert (restored / "usr/bin/gunyahvm").stat().st_mode & 0o111
