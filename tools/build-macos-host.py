#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Build the macOS host runtime from its locked sources with normalized paths.

Inputs: the source archives listed under "host_sources" in
tools/macos.lock.json, the repository patches in
third_party/runtime/macos/, an arm64 build sysroot, Rust 1.88.0, LLVM 18
libclang, meson and the Ubuntu GCC 13 cross compiler. Outputs crosvm,
libvncserver.so.1 and libvirglrenderer.so.1 for the macOS profile. Every
build path is mapped to the reference path, so no host path is embedded.
"""

import argparse
import hashlib
import io
import json
import os
import shlex
import shutil
import subprocess
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(argv, **options):
    subprocess.run([str(a) for a in argv], check=True, **options)


def extract(archive, destination):
    destination.mkdir(parents=True)
    run(["tar", "-xzf", archive, "--strip-components=1", "-C", destination])


def apply(tree, patch):
    run(["git", "init", "-q", tree])
    run(["git", "-C", tree, "apply", "--whitespace=nowarn", patch])


LICENCE_NAMES = ("LICENSE", "LICENCE", "COPYING", "NOTICE", "UNLICENSE", "ADDITIONAL-PERMISSIONS")


def collect_notices(target, src, rust, crosvm, env):
    """Licence texts for every component and Rust crate linked into the payload."""
    for component, names in {
        "crosvm": ("LICENSE", "ADDITIONAL-PERMISSIONS"),
        "crosvm/third_party/minijail": ("LICENSE", "NOTICE"),
        "libvncserver": ("COPYING",),
        "virglrenderer": ("COPYING",),
    }.items():
        for name in names:
            if (src / component / name).is_file():
                destination = target / component.split("/")[-1] / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src / component / name, destination)
    # The offline registry holds only what this build needs, so use cargo tree
    # (normal edges for the target) rather than cargo metadata (all platforms).
    tree = subprocess.check_output(
        [rust / "bin/cargo", "tree", "--offline", "--frozen", "--target", crosvm["target"],
         "--no-default-features", "--features", ",".join(crosvm["features"]),
         "-e", "normal", "--prefix", "none", "-f", "{p}|{l}"],
        cwd=src / "crosvm", env=env, text=True)
    registry = sorted((Path(env["CARGO_HOME"]) / "registry/src").iterdir())
    rows = set()
    for line in tree.splitlines():
        package, _, licence = line.removesuffix(" (*)").partition("|")
        if "(/" in package or not package.strip():
            continue  # crosvm workspace members are covered by crosvm/LICENSE
        name, version = package.split()[:2]
        crate = f"{name}-{version.removeprefix('v')}"
        rows.add(f"{crate} {licence or 'see files'}")
        directory = next(r / crate for r in registry if (r / crate).is_dir())
        found = [f for f in sorted(directory.iterdir()) if f.is_file()
                 and f.name.upper().startswith(LICENCE_NAMES)]
        if not found and not licence:
            raise SystemExit("no licence for crate " + crate)
        for file in found:
            destination = target / "crates" / crate / file.name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(file, destination)
    (target / "crates.txt").write_text("\n".join(sorted(rows)) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True,
                        help="directory with the locked host_sources archives")
    parser.add_argument("--sysroot", type=Path, required=True,
                        help="arm64 sysroot with the declared development packages")
    parser.add_argument("--rust-toolchain", type=Path, required=True)
    parser.add_argument("--host-tools", type=Path, required=True,
                        help="root containing usr/lib/llvm-18/lib (libclang for bindgen)")
    parser.add_argument("--meson", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=len(os.sched_getaffinity(0)))
    args = parser.parse_args()

    lock = json.loads((REPO / "tools/macos.lock.json").read_text())["host_sources"]
    sources = args.sources.resolve()
    for item in lock["archives"].values():
        if digest(sources / item["file"]) != item["sha256"]:
            raise SystemExit("source archive checksum differs: " + item["file"])
    for item in lock["patches"].values():
        if digest(REPO / item["file"]) != item["sha256"]:
            raise SystemExit("patch checksum differs: " + item["file"])
    rust = args.rust_toolchain.resolve()
    for binary in ("rustc", "cargo"):
        version = subprocess.check_output([rust / "bin" / binary, "--version"], text=True).strip()
        if version != lock["toolchains"][binary]:
            raise SystemExit("wrong " + binary + ": " + version)
    meson_version = subprocess.check_output([args.meson, "--version"], text=True).strip()
    if meson_version != lock["toolchains"]["meson"]:
        raise SystemExit("wrong meson: " + meson_version)

    os.umask(0o022)
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    sysroot = args.sysroot.resolve()
    host = args.host_tools.resolve()
    ref = lock["reference_build_path"]
    maps = [f"-ffile-prefix-map={out}={ref}", f"-ffile-prefix-map={sysroot}=/sysroot"]

    src = out / "src"
    for name in ("virglrenderer", "libvncserver", "crosvm"):
        extract(sources / lock["archives"][name]["file"], src / name)
        apply(src / name, REPO / lock["patches"][name]["file"])
    minijail = src / "crosvm/third_party/minijail"
    minijail.unlink()
    extract(sources / lock["archives"]["minijail"]["file"], minijail)

    tools = out / "tool-bin"
    tools.mkdir()
    q = shlex.quote
    cc_flags = [f"--sysroot={sysroot}", "-nostdinc",
                "-isystem", "/usr/lib/gcc-cross/aarch64-linux-gnu/13/include",
                "-isystem", f"{sysroot}/usr/include/aarch64-linux-gnu",
                "-isystem", f"{sysroot}/usr/include",
                f"-B{sysroot}/usr/lib/aarch64-linux-gnu/",
                f"-L{sysroot}/usr/lib/aarch64-linux-gnu",
                f"-Wl,-rpath-link,{sysroot}/usr/lib/aarch64-linux-gnu",
                f"-Wl,-rpath-link,{sysroot}/opt/gunyahvm-3d/graphics/lib", *maps]
    gcc = tools / "aarch64-linux-gnu-gcc"
    gcc.write_text("#!/bin/sh\nexec /usr/bin/aarch64-linux-gnu-gcc-13 "
                   + " ".join(map(q, cc_flags)) + ' "$@"\n')
    gcc.chmod(0o755)
    for binary in ("ar", "nm", "objcopy", "ranlib", "readelf", "strip"):
        (tools / ("aarch64-linux-gnu-" + binary)).symlink_to("/usr/bin/aarch64-linux-gnu-" + binary)
    pkgconfig = ":".join(str(sysroot / p) for p in (
        "opt/gunyahvm-3d/graphics/lib/pkgconfig",
        "usr/lib/aarch64-linux-gnu/pkgconfig", "usr/share/pkgconfig"))
    env = {k: v for k, v in os.environ.items() if k in ("HOME", "USER", "LOGNAME", "TERM", "TMPDIR")}
    env.update(PATH=f"{rust}/bin:{tools}:/usr/bin:/bin", LC_ALL="C", TZ="UTC",
               SOURCE_DATE_EPOCH=str(lock["source_date_epoch"]))

    # virglrenderer: the Droid-VM drm/msm renderer used by the macOS VMM.
    # Same cross file layout as the reference build: plain --sysroot compiler.
    cross = out / "cross.ini"
    lib = f"{sysroot}/usr/lib/aarch64-linux-gnu"
    cross.write_text(
        "[binaries]\n"
        f"c = ['/usr/bin/aarch64-linux-gnu-gcc-13', '--sysroot={sysroot}']\n"
        "ar = '/usr/bin/aarch64-linux-gnu-ar'\n"
        "strip = '/usr/bin/aarch64-linux-gnu-strip'\n"
        "pkg-config = '/usr/bin/pkg-config'\n"
        f"exe_wrapper = ['qemu-aarch64-static', '-L', {str(sysroot)!r}]\n"
        "[host_machine]\nsystem = 'linux'\ncpu_family = 'aarch64'\ncpu = 'aarch64'\nendian = 'little'\n"
        f"[properties]\nsys_root = {str(sysroot)!r}\n"
        f"pkg_config_libdir = [{lib + '/pkgconfig'!r}, {str(sysroot) + '/usr/share/pkgconfig'!r}]\n"
        "needs_exe_wrapper = true\n"
        "[built-in options]\n"
        f"c_args = ['-isystem', '{sysroot}/usr/include/aarch64-linux-gnu', "
        f"'-isystem', '{sysroot}/usr/include', {maps[0]!r}, {maps[1]!r}]\n"
        f"c_link_args = ['-L{lib}', '-L{sysroot}/usr/lib/gcc/aarch64-linux-gnu/13', "
        f"'-Wl,-rpath-link,{lib}']\n")
    virgl = out / "build-virglrenderer"
    run([args.meson, "setup", "--wrap-mode=nodownload", virgl, src / "virglrenderer",
         "--cross-file", cross, *lock["virglrenderer"]["meson_options"]], env=env)
    run(["ninja", "-C", virgl, "-j", args.jobs, "src/libvirglrenderer.so.1.7.7"], env=env)

    # LibVNCServer: private copy for the macOS VMM's high-descriptor sockets.
    vnc = out / "build-libvncserver"
    run(["cmake", "-S", src / "libvncserver", "-B", vnc, "-G", "Unix Makefiles",
         f"-DCMAKE_C_COMPILER={gcc}", "-DCMAKE_SYSTEM_NAME=Linux",
         "-DCMAKE_SYSTEM_PROCESSOR=aarch64", f"-DCMAKE_FIND_ROOT_PATH={sysroot}",
         "-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY", "-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY",
         "-DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER", "-DCMAKE_AR=/usr/bin/aarch64-linux-gnu-ar",
         "-DCMAKE_RANLIB=/usr/bin/aarch64-linux-gnu-ranlib",
         "-DCMAKE_STRIP=/usr/bin/aarch64-linux-gnu-strip",
         *lock["libvncserver"]["cmake_options"]], env=env)
    run(["make", "-C", vnc, "-j", args.jobs, "vncserver"], env=env)

    link = out / "link"
    link.mkdir()
    shutil.copyfile(virgl / "src/libvirglrenderer.so.1.7.7", link / "libvirglrenderer.so.1")
    shutil.copyfile(vnc / "libvncserver.so.0.9.15", link / "libvncserver.so.1")
    (link / "libvirglrenderer.so").symlink_to("libvirglrenderer.so.1")
    (link / "libvncserver.so").symlink_to("libvncserver.so.1")

    # The Gunyah boot shim is a separate bare-metal crate that crosvm embeds
    # with include_bytes!; it has no dependencies and no host paths.
    shim = src / "crosvm/droidvm-shim"
    run([rust / "bin/cargo", "build", "--release", "--frozen", "--offline"],
        cwd=shim, env={**env, "CARGO_HOME": str(out / "shim-cargo-home"),
                       "CARGO_TARGET_DIR": str(out / "shim-target")})
    run(["/usr/bin/aarch64-linux-gnu-objcopy", "-O", "binary",
         out / "shim-target/aarch64-unknown-none-softfloat/release/droidvm-shim",
         src / "crosvm/hypervisor/src/gunyah/shim.bin"])
    if digest(src / "crosvm/hypervisor/src/gunyah/shim.bin") != lock["shim_sha256"]:
        raise SystemExit("Gunyah boot shim differs from the locked build")

    # crosvm: Droid-VM's Gunyah VMM with the GunyahVM macOS patch.
    cargo = out / "cargo-home"
    cargo.mkdir()
    run(["tar", "-xzf", sources / lock["archives"]["cargo_registry"]["file"], "-C", cargo])
    crosvm = lock["crosvm"]
    env.update(
        CARGO_HOME=str(cargo), CARGO_TARGET_DIR=str(out / "target"), CARGO_INCREMENTAL="0",
        RUSTC=str(rust / "bin/rustc"), RUSTDOC=str(rust / "bin/rustdoc"), JOBS=str(args.jobs),
        PKG_VERSION=crosvm["pkg_version"], DEFAULT_PIVOT_ROOT=crosvm["pivot_root"],
        MINIJAIL_BINDGEN_TARGET=crosvm["target"],
        LIBCLANG_PATH=str(host / "usr/lib/llvm-18/lib"),
        LD_LIBRARY_PATH=str(host / "usr/lib/x86_64-linux-gnu"),
        CROSS_COMPILE=str(tools / "aarch64-linux-gnu-"),
        CC_aarch64_unknown_linux_gnu=str(gcc),
        AR_aarch64_unknown_linux_gnu="/usr/bin/aarch64-linux-gnu-ar",
        CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=str(gcc),
        PKG_CONFIG_SYSROOT_DIR_aarch64_unknown_linux_gnu=str(sysroot),
        PKG_CONFIG_LIBDIR_aarch64_unknown_linux_gnu=pkgconfig,
        PKG_CONFIG_ALLOW_CROSS="1",
        BINDGEN_EXTRA_CLANG_ARGS_aarch64_unknown_linux_gnu=(
            f"--sysroot={sysroot} -isystem {host}/usr/lib/llvm-18/lib/clang/18/include "
            f"-isystem {sysroot}/usr/include/aarch64-linux-gnu"),
        CROSVM_USE_SYSTEM_MINIGBM="1", CROSVM_USE_SYSTEM_VIRGLRENDERER="1",
        RUSTFLAGS=" ".join([f"-L native={link}", f"--remap-path-prefix={out}={ref}",
                            f"--remap-path-prefix={rust}=/toolchain",
                            f"--remap-path-prefix={sysroot}=/sysroot",
                            f"--remap-path-prefix={host}=/host-tools"]),
    )
    run([rust / "bin/cargo", "build", "--release", "--frozen", "--offline",
         "--target", crosvm["target"], "--no-default-features",
         "--features", ",".join(crosvm["features"]), "--bin", "crosvm", "-j", args.jobs],
        cwd=src / "crosvm", env=env)

    payload = out / "payload"
    collect_notices(payload / "usr/share/doc/gunyahvm-runtime/licenses/macos", src,
                    rust, crosvm, env)
    outputs = {
        "usr/lib/gunyahvm/macos/crosvm/crosvm": out / "target" / crosvm["target"] / "release/crosvm",
        "usr/lib/gunyahvm/macos/crosvm/libvncserver.so.1": link / "libvncserver.so.1",
        "usr/lib/gunyahvm/macos/renderer/libvirglrenderer.so.1": link / "libvirglrenderer.so.1",
    }
    for relative, built in outputs.items():
        target = payload / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(built, target)
        target.chmod(0o755 if relative.endswith("/crosvm") else 0o644)
        if any(marker in target.read_bytes() for marker in (str(out).encode(), str(sysroot).encode())):
            raise SystemExit("build path leaked into " + relative)
        expected = lock.get("artifacts", {}).get(relative)
        actual = digest(target)
        if expected and expected != actual:
            raise SystemExit(f"{relative}: built {actual}, locked {expected}")
        print(actual + "  " + relative)
    write_input(payload, out / lock["input_filename"], lock["source_date_epoch"])


def write_input(payload, archive, epoch):
    """Deterministic release input: exactly the payload files, root-owned."""
    files = sorted(p for p in payload.rglob("*") if p.is_file())
    members = {}
    with archive.with_suffix("").open("wb") as raw:
        with tarfile.open(fileobj=raw, mode="w|", format=tarfile.GNU_FORMAT) as tar:
            for path in files:
                relative = str(path.relative_to(payload))
                info = tarfile.TarInfo(relative)
                data = path.read_bytes()
                info.size, info.mtime, info.uid, info.gid = len(data), epoch, 0, 0
                info.mode = 0o755 if relative.endswith("/crosvm") else 0o644
                tar.addfile(info, io.BytesIO(data))
                members[relative] = {"size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
    run(["zstd", "-q", "-19", "-f", "--rm", archive.with_suffix(""), "-o", archive])
    spec = {"filename": archive.name, "size": archive.stat().st_size,
            "sha256": digest(archive), "files": members}
    (archive.parent / "input.json").write_text(json.dumps(spec, indent=2) + "\n")
    print(spec["sha256"] + "  " + archive.name)


if __name__ == "__main__":
    main()
