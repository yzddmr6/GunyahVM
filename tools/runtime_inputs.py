# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Install the hash-locked graphics payload; never consume a build directory."""

import gzip
import io
import json
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile

from prebuilt_inputs import fetch, sha, unpack_release

REPO = Path(__file__).resolve().parents[1]
LOCK = REPO / "tools/runtime.lock.json"


def write_archive(path, files):
    """Portable deterministic guest archive, independent of tar implementation."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with (
        path.open("wb") as raw,
        gzip.GzipFile(fileobj=raw, mode="wb", mtime=0, filename="") as gz,
    ):
        with tarfile.open(fileobj=gz, mode="w|", format=tarfile.USTAR_FORMAT) as out:
            for name, data, mode in sorted(files):
                item = tarfile.TarInfo(name)
                item.size, item.mode, item.mtime = len(data), mode, 0
                out.addfile(item, io.BytesIO(data))


def prepare(cache, destination, offline=True):
    lock = json.loads(LOCK.read_text())
    spec = lock["input"]
    archive = fetch(spec, cache, offline)
    destination.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix=".runtime-", dir=destination.parent) as tmp:
        raw = Path(tmp) / "raw"
        unpack_release(archive, raw, spec)
        if sha(raw / "sources.json") != sha(REPO / "third_party/runtime/sources.json"):
            raise ValueError("Runtime source provenance differs from the locked input")

        def copy(source, relative, executable=False):
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(0o755 if executable else 0o644)

        copy(raw / "crosvm", "usr/lib/gunyahvm/crosvm/crosvm", True)
        copy(
            raw / "renderer/libvirglrenderer.so.1",
            "usr/lib/gunyahvm/renderer/libvirglrenderer.so.1",
        )
        copy(
            raw / "renderer/virgl_render_server",
            "usr/lib/gunyahvm/renderer/virgl_render_server",
            True,
        )
        prefix = "usr/share/gunyahvm/guest/"
        for name in ("Image", "initrd.img"):
            copy(raw / "guest" / name, prefix + name)
        with tempfile.TemporaryFile() as modules:
            subprocess.run(
                ["zstd", "-dc", str(raw / "guest/modules.tar.zst")],
                stdout=modules,
                check=True,
            )
            modules.seek(0)
            entries = []
            release = lock["manifest"]["components"]["guest_kernel"]["kernel_release"]
            with tarfile.open(fileobj=modules) as source:
                for member in source:
                    path = Path(member.name)
                    if member.isdir():
                        continue
                    if not member.isfile() or ".." in path.parts:
                        raise ValueError("Unsafe kernel module archive")
                    relative = path.relative_to("usr/lib/modules/" + release)
                    entries.append(
                        (str(relative), source.extractfile(member).read(), 0o644)
                    )
            write_archive(destination / prefix / "modules.tar.gz", entries)
        entries = []
        for path in (raw / "guest/native").rglob("*"):
            if not path.is_file():
                continue
            relative = str(path.relative_to(raw / "guest/native"))
            if relative.startswith("include/") or "/pkgconfig/" in relative:
                continue
            data = path.read_bytes()
            if relative == "share/vulkan/icd.d/freedreno_icd.aarch64.json":
                icd = json.loads(data)
                icd["ICD"]["library_path"] = (
                    "/usr/lib/gunyahvm/guest-mesa/lib/libvulkan_freedreno.so"
                )
                data = (json.dumps(icd, indent=2) + "\n").encode()
            if relative == "share/glvnd/egl_vendor.d/50_mesa.json":
                icd = json.loads(data)
                icd["ICD"]["library_path"] = (
                    "/usr/lib/gunyahvm/guest-mesa/lib/libEGL_mesa.so.0"
                )
                data = (json.dumps(icd, indent=2) + "\n").encode()
            entries.append((relative, data, 0o644))
        write_archive(destination / prefix / "mesa.tar.gz", entries)
        docs = destination / "usr/share/doc/gunyahvm-runtime"
        shutil.copytree(raw / "licenses", docs / "licenses")
        copy(
            REPO / "third_party/runtime/sources.json",
            "usr/share/doc/gunyahvm-runtime/sources.json",
        )
        copy(LOCK, "usr/share/doc/gunyahvm-runtime/runtime-source-lock.json")
        copy(
            raw / "guest/initrd-packages.txt",
            "usr/share/doc/gunyahvm-runtime/initrd-packages.txt",
        )
        expected_paths = {
            p.lstrip("/")
            for c in lock["manifest"]["components"].values()
            for p in c["files"].values()
        }
        if set(lock["outputs"]) != expected_paths:
            raise ValueError("Runtime output inventory differs from its manifest")
        for relative, expected in lock["outputs"].items():
            if sha(destination / relative) != expected:
                raise ValueError("Runtime output checksum mismatch: " + relative)
        manifest = destination / "usr/share/gunyahvm/runtime.json"
        manifest.write_text(json.dumps(lock["manifest"], indent=2) + "\n")
    return destination
