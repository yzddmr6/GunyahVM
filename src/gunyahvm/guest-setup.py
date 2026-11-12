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
import os


def configure_shared_folders(settings, root=Path("/")):
    shares = settings.get("shared_folders", [])
    for share in shares:
        if (not re.fullmatch(r"[A-Za-z0-9_-]{1,32}", share.get("tag", ""))
                or share.get("backend") != "virtiofs" or type(share.get("ro")) is not bool):
            raise ValueError("Invalid shared folder settings")
    fstab = root / "etc/fstab"
    original = fstab.read_text() if fstab.exists() else ""
    begin, end = "# GunyahVM shared folders begin", "# GunyahVM shared folders end"
    if (begin in original) != (end in original):
        raise ValueError("Incomplete GunyahVM shared folder section in fstab")
    managed = re.search(r"(?m)^" + re.escape(begin) + r"\n(.*?)^" + re.escape(end) + r"$", original, flags=re.S)
    previous = set(re.findall(r"(?m)^([A-Za-z0-9_-]{1,32}) /mnt/gunyahvm/", managed[1])) if managed else set()
    original = re.sub(r"(?m)^" + re.escape(begin) + r"\n.*?^" + re.escape(end) + r"\n?", "", original, flags=re.S)
    entries = []
    for share in shares:
        target = "/mnt/gunyahvm/" + share["tag"]
        (root / target.lstrip("/")).mkdir(parents=True, exist_ok=True)
        options = ("ro" if share["ro"] else "rw") + ",nosuid,nodev,nofail,x-systemd.automount,x-systemd.mount-timeout=5s,x-gvfs-show"
        entries.append(f"{share['tag']} {target} virtiofs {options} 0 0")
    if not entries and begin not in (fstab.read_text() if fstab.exists() else ""):
        return
    content = original.rstrip() + "\n"
    if entries:
        content += begin + "\n" + "\n".join(entries) + "\n" + end + "\n"
    fstab.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".fstab-gunyahvm-", dir=fstab.parent)
    try:
        with os.fdopen(fd, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, fstab)
    finally:
        Path(temporary).unlink(missing_ok=True)
    if root == Path("/"):
        for tag in previous - {s["tag"] for s in shares}:
            for suffix in ("automount", "mount"):
                unit = subprocess.check_output(["systemd-escape", "--path", "--suffix=" + suffix, "/mnt/gunyahvm/" + tag], text=True).strip()
                subprocess.run(["systemctl", "stop", unit], check=True)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        for share in shares:
            unit = subprocess.check_output(["systemd-escape", "--path", "--suffix=automount", "/mnt/gunyahvm/" + share["tag"]], text=True).strip()
            subprocess.run(["systemctl", "start", unit], check=True)


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


def setup(seed, root=Path("/")):
    def installed(path):
        return root / path.lstrip("/")

    settings = json.loads((seed / "runtime.json").read_text())
    configure_shared_folders(settings, root)
    username = settings.get("username", "")
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", username):
        raise ValueError("invalid cloud-init username")
    if settings.get("autologin"):
        # Only app-provisioned cloud desktops request this. Discover the display
        # manager location rather than assuming a Fedora path or lib64 ABI.
        for name in ("/etc/gdm3/custom.conf", "/etc/gdm/custom.conf"):
            path = installed(name)
            if path.parent.is_dir():
                parser = configparser.ConfigParser(interpolation=None)
                parser.optionxform = str
                parser.read(path)
                if not parser.has_section("daemon"):
                    parser.add_section("daemon")
                parser["daemon"]["AutomaticLoginEnable"] = "true"
                parser["daemon"]["AutomaticLogin"] = username
                if settings["mode"] in ("virgl", "venus", "native"):
                    parser["daemon"]["DefaultSession"] = (
                        "gunyahvm-" + settings["mode"] + ".desktop"
                    )
                elif parser["daemon"].get("DefaultSession", "").startswith("gunyahvm-"):
                    del parser["daemon"]["DefaultSession"]
                with path.open("w") as stream:
                    parser.write(stream)
                break
    for kind in ("wayland-sessions", "xsessions"):
        for mode in ("virgl", "venus", "native"):
            installed(f"/usr/share/{kind}/gunyahvm-{mode}.desktop").unlink(
                missing_ok=True
            )
    if settings["mode"] == "native":
        mesa = settings["mesa"]
        os_release = dict(
            line.split("=", 1)
            for line in installed("/etc/os-release").read_text().splitlines()
            if "=" in line
        )
        abi = os_release["ID"].strip('"') + ":" + os_release["VERSION_ID"].strip('"')
        if abi != mesa["guest_abi"]:
            raise ValueError("native Mesa payload ABI mismatch: " + abi)
        target = installed("/usr/lib/gunyahvm/guest-mesa")
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=target.parent) as tmp:
            prepared = Path(tmp) / "mesa"
            unpack(seed / "guest-mesa.tar", prepared)
            if not (prepared / mesa["library_subdir"]).is_dir():
                raise ValueError("Mesa library directory missing")
            shutil.rmtree(target, ignore_errors=True)
            prepared.rename(target)
    if settings["mode"] in ("virgl", "venus", "native"):
        mode = settings["mode"]
        for kind in ("wayland-sessions", "xsessions"):
            session = installed(f"/usr/share/{kind}/gunyahvm-{mode}.desktop")
            session.parent.mkdir(parents=True, exist_ok=True)
            session.write_text(
                f"[Desktop Entry]\nName=Ubuntu (GunyahVM {mode})\n"
                f"Exec=/usr/local/bin/gunyahvm-graphics-run --{mode} --desktop\n"
                "TryExec=/usr/bin/gnome-session\nType=Application\n"
                "DesktopNames=ubuntu;GNOME;\nX-GDM-SessionRegisters=true\n"
            )
    if (seed / "guest-modules.tar").is_file():
        version = settings["kernel_release"]
        if platform.release() != version:
            raise ValueError("guest kernel release does not match its module payload")
        destination = installed("/usr/lib/modules") / version
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
