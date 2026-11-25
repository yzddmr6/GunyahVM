#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Build the compiled parts of the macOS boot kit from public sources.

macOS-DroidVM's own public build environment does the work: its
scripts/setup-env.py fetches every source and tool at the commits and hashes
of its configs/env.lock.json, and scripts/product.py builds the XNU kernel,
the board kexts and the Metal plugin. This script pins that repository
(tools/macos.lock.json "kit" "source"), applies GunyahVM's changes (the
ARM_LARGE_MEMORY kernel of a 9 GiB guest), and runs the steps in a container
made from third_party/macos/kit/Dockerfile: Ubuntu 26.04 at a fixed archive
snapshot with the packages the environment lock names.

    build-macos-kit-inputs.py --workspace DIR --output kit-sources.json
        [--mirror DIR] [--save-mirror DIR] [--jobs N]

The workspace is a checkout of macOS-DroidVM at the pinned commit, made on the
first run; later runs reuse what it has fetched and built. --save-mirror
archives the sources maintained by small teams (every checkout and download
the build reads that is not Apple's, Ubuntu's or an LLVM, Rust or EDK II
release) and --mirror restores them into a new workspace before the build, so
a rebuild does not depend on those repositories staying online.

The output names the built files for build-macos-kit.py. Docker must be
usable by the current user; macOS-DroidVM starts its Darling container
(AppleClang, ld-classic) through the same Docker, and both containers run
under the memory limits in the lock. Apple's arm64 kernel collection of
26B123 (--apple-kc) is read once, by the display kext's ABI generator; it is
copied into the workspace and never into an output.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

REPO = Path(__file__).resolve().parents[1]
LOCK = REPO / "tools/macos.lock.json"
# A step that calls setup-env.py's own functions (followed by a Python statement and a closing quote).
SETUP_ENV = ("python3 -c 'import importlib.util as u, argparse as a; s = u.spec_from_file_location(\"e\", "
             "\"scripts/setup-env.py\"); e = u.module_from_spec(s); s.loader.exec_module(e);")


def sha(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def run(argv, **kw):
    print("+", " ".join(map(str, argv)), flush=True)
    return subprocess.run([str(a) for a in argv], check=True, **kw)


def jobs():
    """CPUs not already busy, and 1.5 GiB of available memory per job beyond 6 GiB."""
    with open("/proc/meminfo") as stream:
        avail = next(int(line.split()[1]) for line in stream if line.startswith("MemAvailable:"))
    by_memory = (avail // 1024 - 6144) // 1536
    by_cpu = (os.cpu_count() or 4) - round(os.getloadavg()[0])
    return max(4, min(by_memory, by_cpu))


def prepare(workspace, source):
    """A checkout of the pinned commit with GunyahVM's patches; an existing one is checked."""
    if not (workspace / ".git").exists():
        if workspace.exists() and any(workspace.iterdir()):
            raise SystemExit(f"{workspace}: not empty and not a checkout")
        workspace.mkdir(parents=True, exist_ok=True)
        run(["git", "-C", workspace, "init", "-q"])
        run(["git", "-C", workspace, "remote", "add", "origin", source["repository"]])
        run(["git", "-C", workspace, "fetch", "-q", "--depth", "1", "origin", source["commit"]])
        run(["git", "-C", workspace, "checkout", "-q", "--detach", "FETCH_HEAD"])
    # An existing checkout holds an earlier version of our patches: its tracked files go back to the
    # commit and take these (what the build made is untracked and stays).
    if any(subprocess.run(["git", "-C", str(workspace), "apply", "--reverse", "--check", str(REPO / patch["file"])],
                          capture_output=True).returncode for patch in source["patches"]):
        run(["git", "-C", workspace, "checkout", "-q", "--", "."])
        for patch in source["patches"]:
            run(["git", "-C", workspace, "apply", REPO / patch["file"]])
    for patch in source["xnu_patches"]:
        (workspace / patch["as"]).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(REPO / patch["file"], workspace / patch["as"])
    # What the containers read from this repository (they see only the workspace).
    for item in source["build_files"]:
        target = workspace / item["as"]
        target.parent.mkdir(parents=True, exist_ok=True)
        if sha(REPO / item["file"]) != item["sha256"]:
            raise SystemExit("build file checksum differs: " + item["file"])
        shutil.copyfile(REPO / item["file"], target)
    head = subprocess.check_output(["git", "-C", str(workspace), "rev-parse", "HEAD"], text=True).strip()
    if head != source["commit"]:
        raise SystemExit(f"{workspace}: at {head}, the lock wants {source['commit']}")
    for patch in [*source["patches"], *source["xnu_patches"]]:
        if sha(REPO / patch["file"]) != patch["sha256"]:
            raise SystemExit("patch checksum differs: " + patch["file"])
    for patch in source["xnu_patches"]:
        if sha(workspace / patch["as"]) != patch["sha256"]:
            raise SystemExit(f"{workspace / patch['as']} is not {patch['file']}")
    if sha(workspace / "configs/env.lock.json") != source["env_lock_sha256"]:
        raise SystemExit(f"{workspace}/configs/env.lock.json is not the patched environment lock")


def image(source):
    """The build container's image, tagged with its Dockerfile's hash (never an earlier Dockerfile's image)."""
    container = source["container"]
    dockerfile = REPO / container["dockerfile"]
    if sha(dockerfile) != container["dockerfile_sha256"]:
        raise SystemExit("Dockerfile checksum differs")
    tag = container["image"] + ":" + container["dockerfile_sha256"][:16]
    if subprocess.run(["docker", "image", "inspect", tag], capture_output=True).returncode:
        run(["docker", "build", "-q", "-t", tag, "-f", dockerfile, dockerfile.parent])
    return tag


def contained(workspace, source, script, darling):
    """One step of the build, in the build container: the workspace at its own path, Docker reachable."""
    limits = source["container"]["limits"]
    home = workspace / ".build-home"
    home.mkdir(exist_ok=True)
    env = {"HOME": home, "CCACHE_DIR": workspace / ".ccache", "MACOS_CCACHE": "/usr/bin/ccache",
           "RUSTUP_HOME": workspace / "toolchains/rust/rustup", "CARGO_HOME": workspace / "toolchains/rust/cargo",
           "MACOS_RUST_TOOLCHAIN": source["rust"], "TMPDIR": workspace / "out/tmp",
           "SOURCE_DATE_EPOCH": source["source_date_epoch"],
           # ld64 then records no object time stamps in a debug map (the kernel's N_OSO entries)
           "ZERO_AR_DATE": "1",
           "MACOS_DARLING_CONTAINER": darling, "MACOS_DARLING_IMAGE": darling + ":" + source["container"]["darling_tag"]}
    socket = Path("/var/run/docker.sock")
    argv = ["docker", "run", "--rm", "-i", "--memory", limits["memory"], "--memory-swap", limits["swap"],
            "--user", f"{os.getuid()}:{os.getgid()}", "--group-add", str(socket.stat().st_gid),
            "-v", f"{socket}:{socket}", "-v", f"{workspace}:{workspace}", "-w", workspace,
            *[a for k, v in env.items() for a in ("-e", f"{k}={v}")], source["container"]["tag"],
            "bash", "-c", 'set -euo pipefail; mkdir -p "$TMPDIR"; export PATH="$CARGO_HOME/bin:$PATH"; ' + script]
    run(argv, stdin=subprocess.DEVNULL)


def fixed(directory, mount, source, script, jobs):
    """A step that needs no Darling, its directory at a fixed path: EDK II writes build paths
    into the images it makes, so the images are the same on every machine only this way."""
    limits = source["container"]["limits"]
    run(["docker", "run", "--rm", "-i", "--memory", limits["memory"], "--memory-swap", limits["swap"],
         "--user", f"{os.getuid()}:{os.getgid()}", "-v", f"{directory}:{mount}", "-w", mount,
         "-e", "HOME=/tmp", "-e", f"SOURCE_DATE_EPOCH={source['source_date_epoch']}", "-e", f"JOBS={jobs}",
         source["container"]["tag"], "bash", "-c", "set -euo pipefail; " + script], stdin=subprocess.DEVNULL)


def cap_darling(darling, source):
    """setup-env.py starts the Darling container without limits; give it the build's."""
    if subprocess.run(["docker", "inspect", darling], capture_output=True).returncode == 0:
        limits = source["container"]["limits"]
        run(["docker", "update", "--memory", limits["memory"], "--memory-swap", limits["swap"], darling],
            stdout=subprocess.DEVNULL)


def mirror_entries(workspace, source):
    return [(path, workspace / path) for path in source["mirror"]]


def save_mirror(workspace, source, target):
    target.mkdir(parents=True, exist_ok=True)
    index = {}
    for path, full in mirror_entries(workspace, source):
        if not full.exists():
            raise SystemExit(f"{full}: nothing to save")
        name = path.replace("/", "--") + ".tar.zst"
        with open(target / name, "wb") as raw:
            zstd = subprocess.Popen(["zstd", "-q", "-19", "-T0", "-c"], stdin=subprocess.PIPE, stdout=raw)
            with tarfile.open(fileobj=zstd.stdin, mode="w|", format=tarfile.PAX_FORMAT) as tar:
                tar.add(full, arcname=path)
            zstd.stdin.close()
            if zstd.wait():
                raise SystemExit("zstd failed")
        index[path] = {"file": name, "sha256": sha(target / name)}
    (target / "mirror.json").write_text(json.dumps({"commit": source["commit"], "paths": index}, indent=2) + "\n")


def restore_mirror(workspace, source, mirror):
    index = json.loads((mirror / "mirror.json").read_text())
    if index["commit"] != source["commit"]:
        raise SystemExit(f"{mirror} holds the sources of {index['commit']}")
    wanted = [(mirror / index["paths"][path]["file"], index["paths"][path]["sha256"])
              for path, full in mirror_entries(workspace, source) if not full.exists()]
    for archive, expected in wanted:
        if sha(archive) != expected:
            raise SystemExit(f"{archive}: checksum differs from its index")
    for archive, _ in wanted:
        run(["tar", "-x", "--zstd", "-f", archive, "-C", workspace])


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="kit-sources.json for build-macos-kit.py")
    parser.add_argument("--apple-kc", type=Path, required=True,
                        help="Apple's 26B123 arm64 kernel collection (decoded from the IPSW's kernelcache)")
    parser.add_argument("--fdt", type=Path, required=True, help="the board device tree crosvm dumps for the profile")
    parser.add_argument("--mirror", type=Path)
    parser.add_argument("--save-mirror", type=Path)
    parser.add_argument("--jobs", type=int)
    args = parser.parse_args()
    lock = json.loads(LOCK.read_text())
    source = lock["kit"]["source"]
    workspace = args.workspace.resolve()
    prepare(workspace, source)
    if args.mirror:
        restore_mirror(workspace, source, args.mirror.resolve())
    if sha(args.apple_kc) != source["apple_kc_sha256"]:
        raise SystemExit("--apple-kc is not Apple's 26B123 arm64 kernel collection")
    (workspace / "out/kc").mkdir(parents=True, exist_ok=True)
    if not (workspace / "out/kc/kc27.bin").exists():
        shutil.copyfile(args.apple_kc, workspace / "out/kc/kc27.bin")
    source["container"]["tag"] = image(source)
    darling = source["container"]["darling"]
    n = args.jobs or jobs()
    # Each step skips what it finds done; a step that starts the Darling container is followed by its limits.
    contained(workspace, source, "command -v rustup >/dev/null || rustup-init -y -q --no-modify-path --profile minimal "
              "--default-toolchain none; rustup set auto-self-update disable >/dev/null; "
              f"rustup toolchain list | grep -q '^{source['rust']}' || "
              f"rustup toolchain install {source['rust']} --profile minimal --component rust-src; "
              f"python3 scripts/setup-env.py toolchain --jobs {n}", darling)
    cap_darling(darling, source)
    if subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}", darling],
                      capture_output=True, text=True).stdout.strip() == "false":
        run(["docker", "start", darling], stdout=subprocess.DEVNULL)
    steps = [f"python3 scripts/setup-env.py x12377 --jobs {n}",
             *[f"python3 scripts/setup-env.py sources --part {part} --jobs {n}" for part in ("kernel", "turnip", "bridge", "plugin")],
             f"{SETUP_ENV} [e.git_pinned(e.LOCK[\"git\"][n]) for n in (\"UniXNU\", \"DarwinBoot\", \"edk2\")]'",
             # The kernel's make does not track flags: its objects are always built again (ccache keeps that short).
             "rm -rf out/k27/obj out/k27/sym out/k27/dst",
             f"JOBS={n} python3 scripts/product.py kernel",
             f"JOBS={n} python3 scripts/product.py kexts",
             "python3 scripts/build-droidvm-gpu.py --export-only",
             f"JOBS={n} python3 scripts/product.py gpu --deps"]
    contained(workspace, source, " && ".join(steps), darling)
    # The boot loaders of the "image" part, without its disk tools (an apfs.ko for the build host's kernel), on EDK II
    # with edk2-reproducible.patch. BaseTools serially: VfrCompile's makefile runs one antlr per generated file in a
    # parallel make, and they overwrite each other (edk2-gunyah's build.sh says the same).
    edk2 = "sources/edk2"
    reproducible = "out/gunyahvm/edk2-reproducible.patch"
    fixed(workspace, "/build/macos-droidvm", source,
          f"git -C {edk2} apply --reverse --check ../../{reproducible} 2>/dev/null || git -C {edk2} apply ../../{reproducible}; "
          f"[ -x {edk2}/BaseTools/Source/C/bin/GenFw ] || make -C {edk2}/BaseTools/Source/C -j1 >/dev/null; "
          f"make -C {edk2}/BaseTools/Source/C/GenFw >/dev/null; "
          f"{SETUP_ENV} e.boot_loaders(a.Namespace(jobs={n}))'", n)
    firmware = source["firmware"]
    fixed(workspace / "out/firmware", "/build/fw", source,
          "bash /build/fw/build-firmware.sh " + " ".join(
              [firmware["repository"], firmware["commit"], firmware["edk2"]["repository"], firmware["edk2"]["commit"],
               firmware["version"]]), n)
    outputs = {key: str(workspace / path) for key, path in source["outputs"].items()}
    outputs.update(fdt=str(args.fdt.resolve()))
    missing = [path for path in outputs.values() if not Path(path).is_file()]
    if missing:
        raise SystemExit("not built: " + ", ".join(missing))
    outputs.update(kext_descriptors=str(workspace / "overlay/kext"), kext_root=str(workspace))
    args.output.write_text(json.dumps(outputs, indent=2, sort_keys=True) + "\n")
    if args.save_mirror:
        save_mirror(workspace, source, args.save_mirror.resolve())
    print(args.output)


if __name__ == "__main__":
    main()
