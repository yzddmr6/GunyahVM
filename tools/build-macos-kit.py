#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Build the macOS boot kit: everything GunyahVM adds to Apple's own files.

The kit holds the project's XNU kernel, board kexts (as objects the device
links into Apple's kernel collection), Metal plugin, boot loaders, firmware,
board device tree, mkapfs and the APFS module of the assembly helper. It
contains no Apple file; the device downloads those from Apple and links them
locally (src/gunyahvm/macos_create.py).

    build-macos-kit.py --sources SOURCES.json --output DIR [--jobs N]

SOURCES.json names the prebuilt Apple-toolchain outputs (kernel, kext objects,
plugin) and the open-source loader/firmware builds; their hashes are pinned in
tools/macos.lock.json "kit". The helper module and mkapfs are compiled here
from the pinned sources in inputs/macos-sources.
"""

import argparse
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import struct
import sys
import subprocess
import tarfile
import tempfile

REPO = Path(__file__).resolve().parents[1]
LOCK = REPO / "tools/macos.lock.json"
KIT = "usr/lib/gunyahvm/macos/kit"
EPOCH = 1794009600


def sha(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(argv, **kw):
    print("+", " ".join(map(str, argv)), flush=True)
    return subprocess.run(list(map(str, argv)), check=True, **kw)


PATH = re.compile(rb"/(?:home)/[^/\x00\s]+(?:/[^/\x00\s]+)*")


def scrub_paths(data):
    """Replace build-host paths with an equally long /build/// prefix.

    Strings keep their length, so nothing moves in the binary: the user's home
    and the work directory components (up to the one after the private or
    project directory) become slashes; the rest of the path is kept.
    """
    out = bytearray(data)
    for match in PATH.finditer(data):
        path = match.group()
        parts = path.split(b"/")
        keep = 3  # "", "home", user
        for marker in (b"private", b"project"):
            if marker in parts[keep:]:
                keep = parts.index(marker, keep) + 2
                break
        cut = len(b"/".join(parts[:min(keep, len(parts))]))
        start = match.start()
        out[start:start + cut] = b"/build" + b"/" * (cut - 6)
    return bytes(out)


def adhoc_resign(data):
    """Recompute the code page hashes of every CodeDirectory of a thin Mach-O."""
    out = bytearray(data)
    if out[:4] != b"\xcf\xfa\xed\xfe":
        return data
    count, = struct.unpack_from("<I", out, 16)
    at = 32
    for _ in range(count):
        command, size = struct.unpack_from("<II", out, at)
        if command == 0x1D:
            offset, _ = struct.unpack_from("<II", out, at + 8)
            magic, _, blobs = struct.unpack_from(">III", out, offset)
            assert magic == 0xFADE0CC0
            for index in range(blobs):
                _, start = struct.unpack_from(">II", out, offset + 12 + 8 * index)
                cd = offset + start
                if struct.unpack_from(">I", out, cd)[0] != 0xFADE0C02:
                    continue
                hoff, _, nspecial, ncode, limit, hsize, htype, _, pshift = struct.unpack_from(
                    ">IIIIIBBBB", out, cd + 16)
                algorithm = {1: "sha1", 2: "sha256"}[htype]
                page = 1 << pshift
                for slot in range(ncode):
                    chunk = out[slot * page:min((slot + 1) * page, limit)]
                    digest = hashlib.new(algorithm, chunk).digest()[:hsize]
                    out[cd + hoff + slot * hsize:cd + hoff + (slot + 1) * hsize] = digest
        at += size
    return bytes(out)


def newc(entries):
    """A deterministic SVR4 newc archive: [(name, mode, data or None for dirs)]."""
    out = io.BytesIO()
    ino = 1

    def pad(n):
        out.write(b"\0" * ((4 - n % 4) % 4))

    for name, mode, data in entries + [("TRAILER!!!", 0, b"")]:
        body = b"" if data is None else data
        encoded = name.encode() + b"\0"
        header = "070701" + "".join(f"{v:08X}" for v in (
            ino, mode, 0, 0, 1, 0, len(body), 0, 0, 0, 0, len(encoded), 0))
        out.write(header.encode())
        out.write(encoded)
        pad(110 + len(encoded))
        out.write(body)
        pad(len(body))
        ino += 1
    return out.getvalue()


def build_helper(work, lock, jobs):
    """apfs.ko for the packaged guest kernel, GNU tar for it, and the arm64 mkapfs."""
    sources = REPO / "inputs/macos-sources"
    helper = lock["kit"]["helper_sources"]
    for item in helper["archives"].values():
        if sha(sources / item["file"]) != item["sha256"]:
            raise ValueError("helper source checksum differs: " + item["file"])
    symvers = work / "Module.symvers"
    with open(symvers, "wb") as out:
        subprocess.run(["zstd", "-dc", sources / helper["archives"]["guest-symvers"]["file"]],
                       stdout=out, check=True)
    runtime = json.loads((REPO / "third_party/runtime/sources.json").read_text())
    linux = REPO / "inputs/runtime-sources/linux-guest.tar.gz"
    if sha(linux) != runtime["source_archives"]["linux-guest.tar.gz"]["sha256"]:
        raise ValueError("guest kernel source checksum differs")
    tree, out = work / "linux", work / "kout"
    tree.mkdir()
    out.mkdir()
    run(["tar", "-xzf", linux, "-C", tree])
    config = work / "guest-config"
    run(["tar", "-xf", REPO / "inputs/prebuilt" / json.loads(
        (REPO / "tools/runtime.lock.json").read_text())["input"]["filename"],
        "--zstd", "-C", work, "guest/config"])
    shutil.move(work / "guest/config", config)
    if sha(config) != runtime["guest_kernel"]["config_sha256"]:
        raise ValueError("guest kernel config differs from the runtime lock")
    shutil.copyfile(config, out / ".config")
    make = ["make", "-C", tree, "O=" + str(out), "ARCH=arm64", "CROSS_COMPILE=aarch64-linux-gnu-",
            "LOCALVERSION=", f"-j{jobs}"]
    run(make + ["olddefconfig"], stdout=subprocess.DEVNULL)
    run(make + ["modules_prepare"])
    release = (out / "include/config/kernel.release").read_text().strip()
    if release != runtime["guest_kernel"]["release"]:
        raise ValueError("guest kernel release differs: " + release)
    apfs = work / "apfs"
    apfs.mkdir()
    run(["tar", "-xzf", sources / helper["archives"]["linux-apfs-rw"]["file"], "-C", apfs,
         "--strip-components=1"])
    for patch in helper["patches"]["linux-apfs-rw"]:
        run(["patch", "-p1", "-d", apfs, "-i", REPO / patch])
    # The archive has no git metadata; record the pinned commit instead.
    (apfs / "version.h").write_text(
        '#define GIT_COMMIT "%s"\n' % helper["archives"]["linux-apfs-rw"]["commit"][:12])
    # Exports of a full build of the same kernel source, config and compiler:
    # modpost then rejects any symbol the packaged guest kernel lacks.
    shutil.copyfile(symvers, out / "Module.symvers")
    # No build-directory path may reach the module (WARN/BUG file names).
    maps = (f"-ffile-prefix-map={tree}=/build/linux -ffile-prefix-map={out}=/build/linux-out "
            f"-ffile-prefix-map={apfs}=/build/linux-apfs-rw")
    run(["make", "-C", out, "ARCH=arm64", "CROSS_COMPILE=aarch64-linux-gnu-", "M=" + str(apfs),
         "LOCALVERSION=", f"-j{jobs}", "KCFLAGS=" + maps, "modules"])
    module = work / "apfs.ko"
    run(["aarch64-linux-gnu-strip", "--strip-debug", "-o", module, apfs / "apfs.ko"])
    info = subprocess.run(["modinfo", "-F", "vermagic", module], capture_output=True, text=True,
                          check=True).stdout.split()
    if not info or info[0] != release:
        raise ValueError("apfs.ko vermagic does not match the guest kernel")
    progs = work / "apfsprogs"
    progs.mkdir()
    run(["tar", "-xzf", sources / helper["archives"]["apfsprogs"]["file"], "-C", progs,
         "--strip-components=1"])
    for patch in helper["patches"]["apfsprogs"]:
        run(["patch", "-p1", "-d", progs, "-i", REPO / patch])
    run(["make", "-C", progs / "mkapfs", "CC=aarch64-linux-gnu-gcc", "LDFLAGS=-static",
         "GIT_COMMIT=" + helper["archives"]["apfsprogs"]["commit"][:8], f"-j{jobs}"])
    mkapfs = work / "mkapfs"
    run(["aarch64-linux-gnu-strip", "-o", mkapfs, progs / "mkapfs/mkapfs"])
    deb = work / "tar-deb"
    run(["dpkg-deb", "-x", sources / helper["archives"]["tar"]["file"], deb])
    return module, mkapfs, deb / "usr/bin/tar", release


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=os.cpu_count() or 4)
    parser.add_argument("--update-lock", action="store_true",
                        help="maintainer: record the built file and archive hashes in the lock")
    parser.add_argument("--apple-kc", type=Path,
                        help="with --update-lock: Apple's 26B123 arm64 kernel collection, to pin "
                             "the kernel collection the device will link")
    args = parser.parse_args()
    lock = json.loads(LOCK.read_text())
    spec = lock["kit"]
    sources = json.loads(args.sources.read_text())
    args.output.mkdir(parents=True, exist_ok=False)
    with tempfile.TemporaryDirectory(prefix=".kit-", dir=args.output) as tmp:
        work = Path(tmp)
        root = work / "root"
        kit = root / KIT
        files = {}

        def add(source, relative, mode=0o644):
            target = kit / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            target.chmod(mode)
            files[relative] = sha(target)

        provenance = {}
        for relative, key in spec["prebuilt"].items():
            source = Path(sources[key])
            provenance[relative] = sha(source)
            data = scrub_paths(source.read_bytes())
            if relative.startswith("GPUBundles/") and data != source.read_bytes():
                data = adhoc_resign(data)
            staged = work / "scrubbed" / relative
            staged.parent.mkdir(parents=True, exist_ok=True)
            staged.write_bytes(data)
            add(staged, relative)
        for name in spec["kexts"]:
            descriptor = json.loads(Path(sources["kext_descriptors"], name + ".json").read_text())
            objects = json.loads((Path(sources["kext_root"]) / descriptor["objects"]).read_text())
            relatives = []
            for index, path in enumerate(objects):
                relative = f"kexts/{name}/{index:02d}-{Path(path).name}"
                provenance[relative] = sha(path)
                staged = work / "scrubbed" / relative
                staged.parent.mkdir(parents=True, exist_ok=True)
                staged.write_bytes(scrub_paths(Path(path).read_bytes()))
                add(staged, relative)
                relatives.append(relative)
            descriptor["objects"] = relatives
            target = kit / "kexts" / (name + ".json")
            target.write_text(json.dumps(descriptor, indent=2, sort_keys=True) + "\n")
            files["kexts/" + name + ".json"] = sha(target)
        module, mkapfs, tar, release = build_helper(work, lock, args.jobs)
        add(mkapfs, "bin/mkapfs", 0o755)
        add(module, "helper/apfs.ko")
        add(tar, "helper/tar", 0o755)
        script = (REPO / "src/gunyahvm/macos-helper-init").read_bytes()
        overlay = newc([("gvm", 0o040755, None), ("gvm/apfs.ko", 0o100644, module.read_bytes()),
                        ("usr/bin/tar", 0o100755, tar.read_bytes()),
                        ("gvm-macos-helper", 0o100755, script)])
        (kit / "helper").mkdir(exist_ok=True)
        (kit / "helper/overlay.cpio").write_bytes(overlay)
        files["helper/overlay.cpio"] = sha(kit / "helper/overlay.cpio")
        leaks = [r for r in files if PATH.search((kit / r).read_bytes())]
        if leaks:
            raise ValueError("build-host paths remain in: " + ", ".join(leaks))
        if args.update_lock:
            spec["files"] = dict(sorted(files.items()))
            spec["prebuilt_sources"] = dict(sorted(provenance.items()))
        for relative, expected in spec["files"].items():
            if files.get(relative) != expected:
                raise ValueError(f"kit file {relative}: {files.get(relative)} != pinned {expected}")
        if set(files) != set(spec["files"]):
            raise ValueError("kit inventory differs from the lock: "
                             + ", ".join(sorted(set(files) ^ set(spec["files"]))))
        if args.update_lock and args.apple_kc:
            sys.path.insert(0, str(REPO / "src"))
            from gunyahvm import macos_create
            pinned = {**spec, "files": files}
            (kit / "kit.json").write_text(json.dumps({"schema": 1, "version": spec["version"],
                                                      "kc": spec["kc"], "files": files}))
            linked = work / "kc.bin"
            macos_create.link_kernel_collection(macos_create.Kit(kit), args.apple_kc, linked,
                                                work / "kc.log", verify=False)
            spec["kc"]["sha256"] = sha(linked)
            del pinned
        manifest = {
            "schema": 1, "version": spec["version"], "guest_kernel": release,
            "profile": spec["profile"], "kc": spec["kc"], "boot": spec["boot"],
            "plugin": spec["plugin"], "files": dict(sorted(files.items())),
        }
        (kit / "kit.json").write_text(json.dumps(manifest, indent=2) + "\n")
        archive = args.output / spec["input"]["filename"]
        entries = sorted(p for p in root.rglob("*"))
        with open(archive, "wb") as raw:
            zstd = subprocess.Popen(["zstd", "-q", "-19", "-T0", "-c"], stdin=subprocess.PIPE,
                                    stdout=raw)
            with tarfile.open(fileobj=zstd.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                for path in entries:
                    info = tar.gettarinfo(str(path), str(path.relative_to(root)))
                    info.uid = info.gid = 0
                    info.uname = info.gname = ""
                    info.mtime = EPOCH
                    info.mode = 0o755 if path.is_dir() or os.access(path, os.X_OK) else 0o644
                    if path.is_file():
                        with path.open("rb") as stream:
                            tar.addfile(info, stream)
                    else:
                        tar.addfile(info)
            zstd.stdin.close()
            if zstd.wait():
                raise ValueError("zstd failed")
        if args.update_lock:
            spec["input"].update(size=archive.stat().st_size, sha256=sha(archive))
            LOCK.write_text(json.dumps(lock, indent=2) + "\n")
        elif sha(archive) != spec["input"]["sha256"]:
            raise ValueError("kit archive differs from the lock")
        print(archive, sha(archive), archive.stat().st_size)


if __name__ == "__main__":
    main()
