#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Build the application and default 3D runtime packages from locked inputs."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import subprocess
import tempfile

from windows_openssh import verify_openssh
from prebuilt_inputs import prepare
from runtime_inputs import prepare as prepare_runtime

REPO = Path(__file__).resolve().parents[1]


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def copy(source, root, target, mode=0o644):
    destination = root / target
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, destination)
    destination.chmod(mode)


def stage(root, crosvm, crosvm_licenses, windows_assets, runtime_root=None):
    crosvm_lock = json.loads((REPO / "tools/crosvm.lock.json").read_text())
    windows_lock = json.loads((REPO / "tools/windows.lock.json").read_text())
    if sha(crosvm) != crosvm_lock["artifacts"]["crosvm"]:
        raise ValueError("Unverified crosvm binary")
    for name, expected in windows_lock["artifacts"].items():
        if sha(windows_assets / name) != expected:
            raise ValueError("Unverified Windows asset: " + name)
    verify_openssh(windows_assets, windows_lock["openssh"])
    inventory = json.loads((windows_assets / "driver-files.json").read_text())
    for rel, item in inventory.items():
        if (
            not (windows_assets / rel)
            .resolve()
            .is_relative_to(windows_assets.resolve())
            or sha(windows_assets / rel) != item["sha256"]
        ):
            raise ValueError("Unverified driver: " + rel)
    shutil.copytree(REPO / "packaging", root, dirs_exist_ok=True)
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    copy(crosvm, root, "usr/lib/gunyahvm/crosvm/crosvm", 0o755)
    (root / "usr/share/gunyahvm/crosvm/empty").mkdir(parents=True)
    shutil.copytree(REPO / "data/devices", root / "usr/share/gunyahvm/devices")
    for source in (REPO / "src/gunyahvm").rglob("*"):
        if (
            source.is_file()
            and "__pycache__" not in source.parts
            and source.suffix != ".c"
        ):
            copy(
                source,
                root,
                "usr/lib/gunyahvm/python/gunyahvm/"
                + str(source.relative_to(REPO / "src/gunyahvm")),
            )
    copy(REPO / "bin/gunyahvm", root, "usr/bin/gunyahvm", 0o755)
    for source in (REPO / "libexec").iterdir():
        if not source.is_file():
            continue
        copy(source, root, "usr/libexec/gunyahvm/" + source.name, 0o755)
    shutil.copytree(windows_assets, root / "usr/share/gunyahvm/windows")
    docs = root / "usr/share/doc/gunyahvm"
    docs.mkdir(parents=True)
    shutil.copytree(crosvm_licenses, docs / "licenses")
    # Keep the same attribution files when packaging freshly rebuilt inputs.
    for family, destination in [
        ("licenses", "usr/share/doc/gunyahvm/licenses"),
        ("windows", "usr/share/gunyahvm/windows"),
    ]:
        annotations = REPO / "assets" / family
        for source in annotations.rglob("*.license"):
            copy(source, root, destination + "/" + str(source.relative_to(annotations)))
    for name in [
        "README.md",
        "README.zh-CN.md",
        "LICENSE",
        "NOTICE",
        "CHANGELOG.md",
        "CHANGELOG.zh-CN.md",
    ]:
        copy(REPO / name, root, "usr/share/doc/gunyahvm/" + name)
    shutil.copytree(REPO / "LICENSES", docs / "LICENSES")
    shutil.copytree(REPO / "docs", docs / "guides")
    copy(REPO / "debian/copyright", root, "usr/share/doc/gunyahvm/copyright")
    for name in ["crosvm", "windows", "prebuilt"]:
        copy(
            REPO / f"tools/{name}.lock.json",
            root,
            f"usr/share/doc/gunyahvm/{name}-source-lock.json",
        )
    # Git records executable bits, not checkout umasks. Normalize installed
    # permissions so local builds and clean CI checkouts produce the same modes.
    root.chmod(0o755)
    for path in root.rglob("*"):
        path.chmod(0o755 if path.is_dir() else 0o644)
    for path in [
        root / "usr/bin/gunyahvm",
        root / "usr/lib/gunyahvm/crosvm/crosvm",
        *(root / "usr/libexec/gunyahvm").iterdir(),
    ]:
        path.chmod(0o755)
    runtime = {
        "schema": 1,
        "architecture": "arm64",
        "components": {
            "crosvm": {
                "version": crosvm_lock["pkg_version"],
                "build_features": crosvm_lock["features"],
                "features": [
                    "gunyah",
                    "net",
                    "vhost-net",
                    "vnc",
                    "simplefb",
                    "p9",
                    "input-modes",
                ],
                "files": {"binary": "/usr/lib/gunyahvm/crosvm/crosvm"},
            },
            "uefi": {
                "version": windows_lock["firmware_version"],
                "features": ["windows-ecam"],
                "files": {"image": "/usr/share/gunyahvm/windows/firmware.fd"},
            },
            "windows_openssh": {
                "version": windows_lock["openssh"]["version"],
                "features": ["offline", "key-only"],
                "files": {
                    "archive": "/usr/share/gunyahvm/windows/OpenSSH-ARM64.zip",
                    "lock": "/usr/share/gunyahvm/windows/openssh.json",
                },
            },
            "windows_drivers": {
                "version": windows_lock["drivers"]["source_commit"],
                "features": ["test-signed"],
                "files": {
                    "inventory": "/usr/share/gunyahvm/windows/driver-files.json",
                    "certificate": "/usr/share/gunyahvm/windows/DroidVM_Test.cer",
                    "helper": "/usr/share/gunyahvm/windows/winpe-helper.exe",
                    **{
                        name: "/usr/share/gunyahvm/windows/" + name
                        for name in inventory
                    },
                },
            },
        },
    }
    sys.path.insert(0, str(REPO / "src"))
    from gunyahvm import components

    if runtime_root:
        incoming = components.load(
            runtime_root / "usr/share/gunyahvm/runtime.json", runtime_root
        )
        for name, component in incoming["components"].items():
            for role, installed in component["files"].items():
                source = runtime_root / installed.lstrip("/")
                copy(
                    source,
                    root,
                    installed.lstrip("/"),
                    0o755 if role in ("binary", "render_server") else 0o644,
                )
            runtime["components"][name] = component
        # Runtime builders supply their own notices alongside their payloads.
        notices = runtime_root / "usr/share/doc/gunyahvm-runtime"
        if not notices.is_dir():
            raise ValueError(
                "runtime root requires usr/share/doc/gunyahvm-runtime provenance/licenses"
            )
        shutil.copytree(notices, root / "usr/share/doc/gunyahvm-runtime")
    components.validate(runtime, root)
    (root / "usr/share/gunyahvm/runtime.json").write_text(
        json.dumps(runtime, indent=2) + "\n"
    )
    control = root / "DEBIAN"
    control.mkdir()
    paragraphs = (REPO / "debian/control").read_text().strip().split("\n\n")
    binary = paragraphs[1]
    size = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    (control / "control").write_text(
        binary.replace(
            "Architecture: arm64",
            "Version: 1.0.1~rc3-1\nArchitecture: arm64\nMaintainer: yzddMr6 <yzddmr6@gmail.com>\nHomepage: https://github.com/yzddmr6/GunyahVM\nInstalled-Size: "
            + str((size + 1023) // 1024),
        )
        + "\n"
    )
    for name in ["postinst", "prerm", "postrm"]:
        copy(REPO / "debian" / name, root, "DEBIAN/" + name, 0o755)
    (control / "conffiles").write_text(
        "".join(
            "/" + str(p.relative_to(root)) + "\n"
            for p in sorted((root / "etc").rglob("*"))
            if p.is_file()
        )
    )
    files = [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file() and "DEBIAN" not in p.relative_to(root).parts
    ]
    (control / "md5sums").write_text(
        "".join(
            hashlib.md5(p.read_bytes()).hexdigest()
            + "  "
            + str(p.relative_to(root))
            + "\n"
            for p in files
        )
    )
    return files


def split_packages(root, out):
    """The application owns the complete manifest; payload packages own no VM data."""
    runtime = json.loads((root / "usr/share/gunyahvm/runtime.json").read_text())
    paragraphs = (REPO / "debian/control").read_text().strip().split("\n\n")[2:]
    roots = {"gunyahvm": root}
    owners = {
        "gunyahvm-runtime": ("crosvm", "renderer"),
        "gunyahvm-guest-kernel": ("guest_kernel",),
        "gunyahvm-guest-mesa": ("guest_mesa",),
    }
    for paragraph in paragraphs:
        package = paragraph.splitlines()[0].split(": ")[1]
        tree = out / ("stage-" + package)
        roots[package] = tree
        (tree / "DEBIAN").mkdir(parents=True)
        (tree / "DEBIAN/control").write_text(
            paragraph.replace(
                "Architecture: arm64",
                "Version: 1.0.1~rc3-1\nArchitecture: arm64\nMaintainer: yzddMr6 <yzddmr6@gmail.com>",
            )
            + "\n"
        )
        for component in owners[package]:
            if component not in runtime["components"]:
                raise ValueError("Default package set requires " + component)
            for installed in runtime["components"][component]["files"].values():
                relative = installed.lstrip("/")
                target = tree / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                (root / relative).rename(target)
        copy(REPO / "debian/copyright", tree, "usr/share/doc/" + package + "/copyright")
        # Updating any live runtime payload is refused, including direct dpkg use.
        copy(REPO / "debian/prerm", tree, "DEBIAN/prerm", 0o755)
    notices = root / "usr/share/doc/gunyahvm-runtime"
    shutil.copytree(
        notices,
        roots["gunyahvm-runtime"] / "usr/share/doc/gunyahvm-runtime",
        dirs_exist_ok=True,
    )
    for package, families in {
        "gunyahvm-guest-kernel": (
            "kernel",
            "kernel-COPYING",
            "droidvm-guest-additions-ADDITIONAL-PERMISSIONS",
            "initrd",
        ),
        "gunyahvm-guest-mesa": ("mesa.rst",),
    }.items():
        target = roots[package] / "usr/share/doc" / package / "licenses"
        target.mkdir(parents=True)
        for family in families:
            source = notices / "licenses" / family
            if source.is_dir():
                shutil.copytree(source, target / family)
            else:
                shutil.copyfile(source, target / family)
    shutil.rmtree(notices)
    return roots


def finalize(root):
    files = [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file() and "DEBIAN" not in p.relative_to(root).parts
    ]
    control = root / "DEBIAN/control"
    lines = [
        line
        for line in control.read_text().splitlines()
        if not line.startswith("Installed-Size:")
    ]
    lines.insert(
        2,
        "Installed-Size: " + str((sum(p.stat().st_size for p in files) + 1023) // 1024),
    )
    control.write_text("\n".join(lines) + "\n")
    (root / "DEBIAN/md5sums").write_text(
        "".join(
            hashlib.md5(p.read_bytes()).hexdigest()
            + "  "
            + str(p.relative_to(root))
            + "\n"
            for p in files
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--input-cache", type=Path, default=REPO / "inputs/prebuilt")
    parser.add_argument("--offline", action="store_true")
    parser.add_argument(
        "--runtime-root",
        type=Path,
        help="staged runtime.json and payloads to overlay after capability validation",
    )
    parser.add_argument("--crosvm-build", type=Path)
    parser.add_argument("--windows-build", type=Path)
    args = parser.parse_args()
    if bool(args.crosvm_build) != bool(args.windows_build):
        parser.error("source builds require both --crosvm-build and --windows-build")
    os.umask(0o022)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    root = out / "stage"
    os.environ["SOURCE_DATE_EPOCH"] = "1794009600"
    with tempfile.TemporaryDirectory(prefix=".package-", dir=out) as tmp:
        tmp = Path(tmp)
        if args.crosvm_build:
            if not args.windows_build:
                parser.error("source builds require --windows-build")
            crosvm = args.crosvm_build / "crosvm"
            windows = args.windows_build / "assets"
            licenses = tmp / "licenses"
            for directory, label in [
                (args.crosvm_build / "src", "crosvm"),
                (args.crosvm_build / "src/third_party/minijail", "minijail"),
            ]:
                copy(directory / "LICENSE", licenses, label + "/LICENSE")
            for name in ["LICENSE.GPL-2.0", "ADDITIONAL-PERMISSIONS", "CREDITS"]:
                copy(args.crosvm_build / "src" / name, licenses, "crosvm/" + name)
            for crate in sorted(
                (args.crosvm_build / "cargo-home/registry/src").glob("*/*")
            ):
                for file in crate.rglob("*"):
                    if file.is_file() and file.name.upper().startswith(
                        ("LICENSE", "LICENCE", "COPYING", "NOTICE")
                    ):
                        copy(
                            file,
                            licenses,
                            crate.name + "/" + str(file.relative_to(crate)),
                        )
        else:
            assets = prepare(args.input_cache, tmp / "assets", args.offline)
            crosvm, licenses, windows = (
                assets / "crosvm",
                assets / "licenses",
                assets / "windows",
            )
        runtime_root = args.runtime_root or prepare_runtime(
            args.input_cache, tmp / "runtime", args.offline
        )
        stage(root, crosvm, licenses, windows, runtime_root)
    roots = split_packages(root, out)
    sums = []
    manifests = {}
    for package, tree in roots.items():
        finalize(tree)
        deb = out / f"{package}_1.0.1~rc3-1_arm64.deb"
        subprocess.run(
            [
                "dpkg-deb",
                "--root-owner-group",
                "-Zxz",
                "-z1",
                "--threads-max=3",
                "--build",
                str(tree),
                str(deb),
            ],
            check=True,
        )
        sums.append(sha(deb) + "  " + deb.name + "\n")
        manifests[package] = {
            str(p.relative_to(tree)): sha(p)
            for p in tree.rglob("*")
            if p.is_file() and "DEBIAN" not in p.relative_to(tree).parts
        }
        print(deb)
    (out / "SHA256SUMS").write_text("".join(sums))
    (out / "manifest.json").write_text(json.dumps(manifests, indent=2) + "\n")


if __name__ == "__main__":
    main()
