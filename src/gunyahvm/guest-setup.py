#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Guest payload/graphics setup adapted from iceland-vms (mzakamin-hub)
# d628b9b25d08e074b34a2f1a6113ab23767b78d7, gvm-agent --setup.
"""Install only the runtime payload on the VM's NoCloud seed, into private prefixes."""

import json
import configparser
import platform
import re
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile


def unpack(archive, target):
    # Payload archives contain relative regular files/directories/safe symlinks.
    # Do not extract owners, device nodes or absolute/traversing names as guest root.
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(archive) as stream:
        for item in stream.getmembers():
            name = Path(item.name)
            if (
                name.is_absolute()
                or ".." in name.parts
                or not (item.isfile() or item.isdir() or item.issym())
            ):
                raise ValueError("unsafe runtime archive member")
            if item.issym() and (
                Path(item.linkname).is_absolute() or ".." in Path(item.linkname).parts
            ):
                raise ValueError("unsafe runtime archive link")
        # No writing through an archive-created symlink on any supported Python.
        for item in stream.getmembers():
            destination = target / item.name
            if any(p.is_symlink() for p in destination.parents if p != target.parent):
                raise ValueError("runtime member parent is a symlink")
            if item.isdir():
                destination.mkdir(parents=True, exist_ok=True)
            elif item.issym():
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.symlink_to(item.linkname)
            else:
                destination.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(item) as source, destination.open("xb") as out:
                    shutil.copyfileobj(source, out)
                destination.chmod(item.mode & 0o755)


def setup(seed):
    settings = json.loads((seed / "runtime.json").read_text())
    username = settings.get("username", "")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username):
        raise ValueError("invalid cloud-init username")
    if settings.get("autologin"):
        # Only app-provisioned cloud desktops request this. Discover the display
        # manager location rather than assuming a Fedora path or lib64 ABI.
        for name in ("/etc/gdm3/custom.conf", "/etc/gdm/custom.conf"):
            path = Path(name)
            if path.parent.is_dir():
                parser = configparser.ConfigParser(interpolation=None)
                parser.optionxform = str
                parser.read(path)
                if not parser.has_section("daemon"):
                    parser.add_section("daemon")
                parser["daemon"]["AutomaticLoginEnable"] = "true"
                parser["daemon"]["AutomaticLogin"] = username
                with path.open("w") as stream:
                    parser.write(stream)
                break
    linker = Path("/etc/ld.so.conf.d/gunyahvm-guest-mesa.conf")
    icd = Path("/etc/vulkan/icd.d/gunyahvm-native.json")
    if settings["mode"] != "native":
        changed = linker.exists() or icd.exists()
        linker.unlink(missing_ok=True)
        icd.unlink(missing_ok=True)
        if changed:
            subprocess.run(["ldconfig"], check=True)
    else:
        mesa = settings["mesa"]
        os_release = dict(
            line.split("=", 1)
            for line in Path("/etc/os-release").read_text().splitlines()
            if "=" in line
        )
        abi = os_release["ID"].strip('"') + ":" + os_release["VERSION_ID"].strip('"')
        if abi != mesa["guest_abi"]:
            raise ValueError("native Mesa payload ABI mismatch: " + abi)
        target = Path("/usr/lib/gunyahvm/guest-mesa")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
            prepared = Path(tmp) / "mesa"
            unpack(seed / "guest-mesa.tar", prepared)
            if not (prepared / mesa["library_subdir"]).is_dir():
                raise ValueError("Mesa library directory missing")
            shutil.rmtree(target, ignore_errors=True)
            prepared.rename(target)
        linker.write_text(str(target / mesa["library_subdir"]) + "\n")
        icd.parent.mkdir(parents=True, exist_ok=True)
        # Payload ICD uses the final private prefix; never overwrite distro ICDs.
        shutil.copyfile(target / mesa["icd"], icd)
        subprocess.run(["ldconfig"], check=True)
    if (seed / "guest-modules.tar").is_file():
        version = settings["kernel_release"]
        if platform.release() != version:
            raise ValueError("guest kernel release does not match its module payload")
        destination = Path("/usr/lib/modules") / version
        # Modules archive has paths relative to this exact kernel release.
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=destination.parent) as tmp:
                prepared = Path(tmp) / "modules"
                unpack(seed / "guest-modules.tar", prepared)
                prepared.rename(destination)
            subprocess.run(["depmod", version], check=True)
        for module in settings.get("guest_modules", []):
            subprocess.run(["modprobe", module], check=True)


if __name__ == "__main__":
    mount = Path("/run/gunyahvm-seed")
    mount.mkdir(exist_ok=True)
    subprocess.run(
        ["mount", "-t", "iso9660", "-o", "ro", "/dev/disk/by-label/cidata", str(mount)],
        check=True,
    )
    try:
        setup(mount)
    finally:
        subprocess.run(["umount", str(mount)], check=True)
