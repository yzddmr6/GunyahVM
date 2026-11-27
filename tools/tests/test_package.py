# SPDX-License-Identifier: MIT
# Copyright (C) 2026 yzddMr6
"""Standalone package payload, identity and source-build route integration tests."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest
from gunyahvm import DEB_VERSION


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
        "gir1.2-casilda-1.0 (>= 1.4.0-1+gunyahvm4)",
        "wimtools",
        "7zip",
        "ntfs-3g",
        "python3-hivex",
        "dosfstools",
        "mtools",
    ]:
        assert name in depends.split(": ", 1)[1].split(", ")
    assert "remmina" not in depends
    for name in ("liblzfse1", "python3-cryptography", "libvulkan1", "libdrm-freedreno1"):
        assert name in depends.split(": ", 1)[1].split(", ")
    assert "Conflicts:" not in fields
    for field in ("Breaks", "Replaces"):
        line = next(x for x in fields.splitlines() if x.startswith(field + ":"))
        for old in ("gunyahvm-runtime", "gunyahvm-guest-kernel", "gunyahvm-guest-mesa"):
            assert f"{old} (<< 1.3.0~)" in line
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
    shutil.copyfile(package[1] / "usr/lib/gunyahvm/crosvm/crosvm", crosvm / "crosvm")
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
    for original_deb in original.glob("*.deb"):
        assert hashlib.sha256(original_deb.read_bytes()).digest() == hashlib.sha256(
            (output / original_deb.name).read_bytes()
        ).digest(), original_deb.name


def test_one_package_holds_runtime_guest_payloads_and_macos_kit(package):
    deb, restored, output = package
    assert [p.name for p in output.glob("*.deb")] == [deb.name]
    from gunyahvm import components

    manifest = components.load(restored / "usr/share/gunyahvm/runtime.json", restored)
    assert manifest["components"]["renderer"]["features"] == [
        "virgl",
        "venus",
        "drm-msm",
    ]
    for name in ("guest_kernel", "guest_mesa", "macos"):
        assert name in manifest["components"]
    macos = manifest["components"]["macos"]
    assert "create-from-apple" in macos["features"]
    kit = json.loads((restored / macos["files"]["kit"].lstrip("/")).read_text())
    lock = json.loads((REPO / "tools/macos.lock.json").read_text())["kit"]
    assert kit["files"] == lock["files"]
    root = restored / "usr/lib/gunyahvm/macos/kit"
    for relative, digest in kit["files"].items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest
    assert (root / "bin/mkapfs").stat().st_mode & 0o777 == 0o755
    assert macos["files"]["firmware"] == "/usr/lib/gunyahvm/macos/kit/firmware.fd"
    assert list(json.loads((output / "manifest.json").read_text())) == ["gunyahvm"]


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


def test_graphics_vmm_omits_private_source_paths(package):
    import re

    _, installed, _ = package
    binary = (installed / "usr/lib/gunyahvm/crosvm/crosvm").read_bytes()
    assert not re.search(rb"/(?:home/[^/\s\0]+|Users/[^/\s\0]+|root)/[^\0\s]{1,512}?\.rs\b", binary)


def test_all_guests_share_one_vmm_and_renderer(package):
    _, restored, _ = package
    files = [p for p in restored.rglob('*') if p.is_file()]
    assert [str(p.relative_to(restored)) for p in files if p.name == 'crosvm'] == [
        'usr/lib/gunyahvm/crosvm/crosvm']
    assert [str(p.relative_to(restored)) for p in files if p.name.startswith('libvirglrenderer.so')] == [
        'usr/lib/gunyahvm/renderer/libvirglrenderer.so.1']
    runtime = json.loads((restored / 'usr/share/gunyahvm/runtime.json').read_text())
    assert set(runtime['components']['macos']['files']) == {'kit', 'firmware'}
    assert {'windows-wddm', 'virtio-mmio', 'pci-msi'} <= set(runtime['components']['crosvm']['features'])
    assert 'venus' in runtime['components']['renderer']['features']
