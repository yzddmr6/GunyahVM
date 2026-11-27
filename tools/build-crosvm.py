#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (C) 2026 yzddMr6
"""Build the shared ARM64 crosvm, virglrenderer and LibVNCServer offline."""

import argparse, hashlib, json, os, shlex, shutil, subprocess
from pathlib import Path


def run(args, **kw):
    return subprocess.run([str(x) for x in args], check=True, **kw)


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def build_graphics(repo, sources, out, sysroot, gcc, meson, jobs, env):
    lock = json.loads((repo / "third_party/runtime/sources.json").read_text())
    src = out / "graphics-source"
    for name, archive in (("virglrenderer", "virglrenderer-patched.tar.gz"),
                          ("libvncserver", lock["libvncserver"]["file"])):
        spec = lock["source_archives"][archive]
        if digest(sources / archive) != spec["sha256"]:
            raise RuntimeError("source checksum differs: " + archive)
        tree = src / name
        tree.mkdir(parents=True)
        run(["tar", "-xzf", sources / archive, "--strip-components=1", "-C", tree])
        run(["git", "init", "-q", tree])
        patches = lock["patches"][name]
        for patch in patches:
            if digest(repo / patch["file"]) != patch["sha256"]:
                raise RuntimeError("patch checksum differs: " + patch["file"])
        for patch in patches[spec.get("applied_patch_count", 0):]:
            run(["git", "-C", tree, "apply", repo / patch["file"]])
    version = subprocess.check_output([meson, "--version"], text=True).strip()
    if version != lock["toolchains"]["meson"]:
        raise RuntimeError("wrong meson: " + version)
    cross = out / "cross.ini"
    lib = sysroot / "usr/lib/aarch64-linux-gnu"
    cross.write_text(
        "[binaries]\n"
        f"c = {str(gcc)!r}\nar = '/usr/bin/aarch64-linux-gnu-ar'\n"
        "strip = '/usr/bin/aarch64-linux-gnu-strip'\npkg-config = '/usr/bin/pkg-config'\n"
        "[host_machine]\nsystem = 'linux'\ncpu_family = 'aarch64'\ncpu = 'aarch64'\nendian = 'little'\n"
        f"[properties]\nsys_root = {str(sysroot)!r}\nneeds_exe_wrapper = true\n"
        f"pkg_config_libdir = [{str(lib / 'pkgconfig')!r}, {str(sysroot / 'usr/share/pkgconfig')!r}]\n")
    virgl = out / "build-virglrenderer"
    run([meson, "setup", "--wrap-mode=nodownload", virgl, src / "virglrenderer",
         "--cross-file", cross, "--prefix=/opt/gunyahvm-3d/graphics", "--libdir=lib",
         "--libexecdir=libexec", "-Ddrm-renderers=msm", "-Dvenus=true", "-Dplatforms=egl",
         "-Dbuildtype=release", "-Dtests=false"], env=env)
    run(["ninja", "-C", virgl, "-j", jobs], env=env)
    run([meson, "install", "-C", virgl, "--no-rebuild"], env={**env, "DESTDIR": str(sysroot)})
    vnc = out / "build-libvncserver"
    run(["cmake", "-S", src / "libvncserver", "-B", vnc, "-G", "Unix Makefiles",
         f"-DCMAKE_C_COMPILER={gcc}", "-DCMAKE_SYSTEM_NAME=Linux",
         "-DCMAKE_SYSTEM_PROCESSOR=aarch64", f"-DCMAKE_FIND_ROOT_PATH={sysroot}",
         "-DCMAKE_FIND_ROOT_PATH_MODE_INCLUDE=ONLY", "-DCMAKE_FIND_ROOT_PATH_MODE_LIBRARY=ONLY",
         "-DCMAKE_FIND_ROOT_PATH_MODE_PROGRAM=NEVER", "-DCMAKE_AR=/usr/bin/aarch64-linux-gnu-ar",
         "-DCMAKE_RANLIB=/usr/bin/aarch64-linux-gnu-ranlib",
         *lock["libvncserver"]["cmake_options"]], env=env)
    run(["make", "-C", vnc, "-j", jobs, "vncserver"], env=env)
    # Link and ship the same poll-based VNC implementation for all guests.
    shutil.copyfile(vnc / "libvncserver.so.0.9.15", lib / "libvncserver.so.1")
    renderer = out / "renderer"
    renderer.mkdir()
    for source, target in (
        (virgl / "src/libvirglrenderer.so.1.11.0", renderer / "libvirglrenderer.so.1"),
        (virgl / "server/virgl_render_server", renderer / "virgl_render_server"),
        (vnc / "libvncserver.so.0.9.15", out / "libvncserver.so.1"),
    ):
        shutil.copyfile(source, target)
        run(["aarch64-linux-gnu-strip", "--strip-unneeded", target])
    (renderer / "virgl_render_server").chmod(0o755)
    run(["patchelf", "--set-rpath", "$ORIGIN", renderer / "virgl_render_server"])
    for name, filename in (("virglrenderer", "COPYING"), ("libvncserver", "COPYING")):
        target = out / "licenses" / name
        target.mkdir(parents=True)
        shutil.copyfile(src / name / filename, target / filename)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for n in ["inputs", "cargo-cache", "rust-toolchain", "runtime-sources", "meson", "output"]:
        p.add_argument("--" + n, type=Path, required=True)
    a = p.parse_args()
    repo = Path(__file__).resolve().parents[1]
    lock = json.loads((repo / "tools/crosvm.lock.json").read_text())
    os.setpriority(os.PRIO_PROCESS, 0, max(10, os.getpriority(os.PRIO_PROCESS, 0)))
    os.umask(0o022)
    out = a.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    rust = a.rust_toolchain.resolve()
    inputs = a.inputs.resolve()
    for name, version in lock["host_packages"].items():
        actual = subprocess.check_output(
            ["dpkg-query", "-W", "-f=${Version}", name], text=True
        )
        if actual != version:
            raise RuntimeError("locked build host package differs: " + name)
    for binary in ["rustc", "cargo"]:
        if (
            subprocess.check_output(
                [str(rust / "bin" / binary), "--version"], text=True
            ).strip()
            != lock[binary]
        ):
            raise RuntimeError("wrong " + binary)
    for rel, expected in lock.get("rust_toolchain_files", {}).items():
        if digest(rust / rel) != expected:
            raise RuntimeError("locked Rust component differs: " + rel)
    for rel, expected in lock["inputs"].items():
        if digest(inputs / rel) != expected:
            raise RuntimeError("input checksum mismatch: " + rel)
    for patch in lock["patches"]:
        if digest(repo / patch["file"]) != patch["sha256"]:
            raise RuntimeError("patch checksum mismatch: " + patch["file"])
    src = out / "src"
    src.mkdir()
    run(
        [
            "tar",
            "-xzf",
            inputs / "crosvm-96e84c3.tar.gz",
            "--strip-components=1",
            "-C",
            src,
        ]
    )
    run(["git", "init", "-q", src])
    for patch in lock["patches"]:
        run(["git", "-C", src, "apply", repo / patch["file"]])
    run(
        [
            "tar",
            "-xzf",
            inputs / "minijail-7845d899.tar.gz",
            "--strip-components=1",
            "-C",
            src / "third_party/minijail",
        ]
    )
    if digest(src / "Cargo.lock") != lock["cargo_lock_sha256"]:
        raise RuntimeError("Cargo.lock differs")
    sysroot = out / "sysroot"
    host = out / "host"
    sysroot.mkdir()
    host.mkdir()
    for directory, dest in [("sysroot-debs", sysroot), ("host-debs", host)]:
        for deb in sorted((inputs / directory).glob("*.deb")):
            run(["dpkg-deb", "-x", deb, dest])
    if not (sysroot / "lib").exists():
        (sysroot / "lib").symlink_to("usr/lib")
    cargo = out / "cargo-home"
    cargo.mkdir()
    shutil.copytree(a.cargo_cache.resolve() / "registry", cargo / "registry")
    wrappers = out / "tool-bin"
    wrappers.mkdir()
    q = shlex.quote
    wrapper = (
        "#!/bin/sh\nexec /usr/bin/aarch64-linux-gnu-gcc "
        + " ".join(
            map(
                q,
                [
                    f"--sysroot={sysroot}",
                    "-nostdinc",
                    "-isystem",
                    "/usr/lib/gcc-cross/aarch64-linux-gnu/13/include",
                    "-isystem",
                    str(sysroot / "usr/include/aarch64-linux-gnu"),
                    "-isystem",
                    str(sysroot / "usr/include"),
                    f"-B{sysroot}/usr/lib/aarch64-linux-gnu/",
                    f"-L{sysroot}/usr/lib/aarch64-linux-gnu",
                    f"-Wl,-rpath-link,{sysroot}/usr/lib/aarch64-linux-gnu",
                    f"-Wl,-rpath-link,{sysroot}/opt/gunyahvm-3d/graphics/lib",
                    f"-ffile-prefix-map={sysroot}=/sysroot",
                    f"-ffile-prefix-map={out}={lock['reference_build_path']}",
                    f"-fdebug-prefix-map={out}={lock['reference_build_path']}",
                ],
            )
        )
        + ' "$@"\n'
    )
    (wrappers / "aarch64-linux-gnu-gcc").write_text(wrapper)
    (wrappers / "aarch64-linux-gnu-gcc").chmod(0o755)
    for binary in ["ar", "nm", "objcopy", "ranlib", "readelf", "strip"]:
        (wrappers / ("aarch64-linux-gnu-" + binary)).symlink_to(
            "/usr/bin/aarch64-linux-gnu-" + binary
        )
    (wrappers / "aarch64-linux-gnu-pkg-config").write_text(
        "#!/bin/sh\nexport PKG_CONFIG_SYSROOT_DIR="
        + q(str(sysroot))
        + "\nexport PKG_CONFIG_LIBDIR="
        + q(
            str(sysroot / "opt/gunyahvm-3d/graphics/lib/pkgconfig")
            + ":" + str(sysroot / "usr/lib/aarch64-linux-gnu/pkgconfig")
            + ":"
            + str(sysroot / "usr/share/pkgconfig")
        )
        + '\nexec /usr/bin/pkg-config "$@"\n'
    )
    (wrappers / "aarch64-linux-gnu-pkg-config").chmod(0o755)
    env = {
        k: v for k, v in os.environ.items() if k in ["HOME", "USER", "LOGNAME", "TERM", "TMPDIR"]
    }
    env.update(
        PATH=str(rust / "bin") + ":" + str(wrappers) + ":/usr/bin:/bin",
        LC_ALL="C",
        TZ="UTC",
        SOURCE_DATE_EPOCH=str(lock["source_date_epoch"]),
        CARGO_HOME=str(cargo),
        CARGO_TARGET_DIR=str(out / "target"),
        CARGO_INCREMENTAL="0",
        RUSTC=str(rust / "bin/rustc"),
        RUSTDOC=str(rust / "bin/rustdoc"),
        JOBS=str(min(8, len(os.sched_getaffinity(0)))),
        PKG_VERSION=lock["pkg_version"],
        DEFAULT_PIVOT_ROOT=lock["reference_pivot_root"],
        MINIJAIL_BINDGEN_TARGET=lock["target"],
        LIBCLANG_PATH=str(host / "usr/lib/llvm-18/lib"),
        LD_LIBRARY_PATH=str(host / "usr/lib/x86_64-linux-gnu"),
        CROSS_COMPILE=str(wrappers / "aarch64-linux-gnu-"),
        CC_aarch64_unknown_linux_gnu=str(wrappers / "aarch64-linux-gnu-gcc"),
        AR_aarch64_unknown_linux_gnu="/usr/bin/aarch64-linux-gnu-ar",
        CARGO_TARGET_AARCH64_UNKNOWN_LINUX_GNU_LINKER=str(
            wrappers / "aarch64-linux-gnu-gcc"
        ),
        PKG_CONFIG_SYSROOT_DIR_aarch64_unknown_linux_gnu=str(sysroot),
        PKG_CONFIG_LIBDIR_aarch64_unknown_linux_gnu=str(sysroot / "opt/gunyahvm-3d/graphics/lib/pkgconfig")
        + ":" + str(sysroot / "usr/lib/aarch64-linux-gnu/pkgconfig")
        + ":"
        + str(sysroot / "usr/share/pkgconfig"),
        PKG_CONFIG_ALLOW_CROSS="1",
        BINDGEN_EXTRA_CLANG_ARGS_aarch64_unknown_linux_gnu=f"--sysroot={sysroot} -isystem {host}/usr/lib/llvm-18/lib/clang/18/include -isystem {sysroot}/usr/include/aarch64-linux-gnu",
        RUSTFLAGS=f"--remap-path-prefix={out}={lock['reference_build_path']} --remap-path-prefix={rust}=/toolchain",
    )
    build_graphics(repo, a.runtime_sources.resolve(), out, sysroot,
                   wrappers / "aarch64-linux-gnu-gcc", a.meson.resolve(), env["JOBS"], env)
    run(
        [
            rust / "bin/cargo",
            "build",
            "--release",
            "--frozen",
            "--offline",
            "--target",
            lock["target"],
            "--no-default-features",
            "--features",
            ",".join(lock["features"]),
            "--bin",
            "crosvm",
            "-j",
            env["JOBS"],
        ],
        cwd=src,
        env=env,
    )
    shutil.copyfile(out / "target" / lock["target"] / "release/crosvm", out / "crosvm")
    (out / "crosvm").chmod(0o755)
    run(["aarch64-linux-gnu-strip", "--strip-unneeded", out / "crosvm"])
    run(["patchelf", "--set-rpath", "$ORIGIN:$ORIGIN/../renderer", out / "crosvm"])
    for name, expected in lock.get("artifacts", {}).items():
        if digest(out / name) != expected:
            raise RuntimeError("build differs from locked artifact: " + name)
    print("crosvm SHA256 " + digest(out / "crosvm"))


if __name__ == "__main__":
    main()
