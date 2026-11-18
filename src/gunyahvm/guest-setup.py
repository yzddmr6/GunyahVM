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
import sys
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


def select_user_session(root, username, mode):
    """Record the graphics session where an automatic login reads it.

    GDM starts an automatic login with the user's AccountsService session and
    falls back to the distribution session, not to [daemon] DefaultSession.
    A session left from another graphics mode no longer exists and is dropped.
    """
    path = root / "var/lib/AccountsService/users" / username
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    parser.read(path)
    if not parser.has_section("User"):
        parser.add_section("User")
    user = parser["User"]
    if mode in ("virgl", "venus", "native"):
        user["Session"] = "gunyahvm-" + mode
    elif user.get("Session", "").startswith("gunyahvm-"):
        del user["Session"]
    else:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as stream:
        parser.write(stream, space_around_delimiters=False)
    path.chmod(0o600)


def align_network_wait(root=Path("/"), run=subprocess.run):
    """Let network-online.target wait only for the manager that owns the network.

    Installing the desktop on the cloud image makes NetworkManager the netplan
    renderer, while the image keeps systemd-networkd-wait-online enabled. When
    netplan gave every interface to NetworkManager and nothing configures
    systemd-networkd, that service waits for links it never manages until its
    120 s timeout, and the display manager starts only after it gives up.
    NetworkManager-wait-online still holds network-online.target until the
    network is up. Stopping the service also releases the boot in progress.
    """
    unit = "systemd-networkd-wait-online.service"
    managed_by_nm = list(
        (root / "run/NetworkManager/system-connections").glob("netplan-*.nmconnection")
    )
    networkd = [
        path
        for directory in ("run/systemd/network", "etc/systemd/network")
        for path in (root / directory).glob("*.network")
    ]
    if not managed_by_nm or networkd:
        return
    if run(["systemctl", "--quiet", "is-enabled", unit], check=False).returncode == 0:
        run(["systemctl", "disable", unit], check=False)
    run(["systemctl", "stop", unit], check=False)


DESKTOP_STATE = "var/lib/gunyahvm/desktop.json"
DESKTOP_ANCHOR = "ubuntu-desktop-minimal"
APT_ENV = {"DEBIAN_FRONTEND": "noninteractive"}
# dpkg asks about a configuration file that is already on disk and differs from the package's,
# such as /etc/gdm3/custom.conf, which the guest setup writes for automatic login. Nobody can
# answer in the guest: dpkg reads end of file and leaves the package unconfigured. Keep the file
# on disk, as dpkg's default does, without asking.
DPKG_CONFFILES = ["--force-confdef", "--force-confold"]
# Unpacked but not configured: an install or upgrade that stopped, which dpkg --configure -a ends.
UNFINISHED = {"half-installed", "unpacked", "half-configured"}


def dpkg_states(name, root):
    """NAME's states in dpkg's database (installed, unpacked, config-files...), one per architecture."""
    try:
        stanzas = (root / "var/lib/dpkg/status").read_text(errors="replace").split("\n\n")
    except OSError:
        return set()
    states = set()
    for stanza in stanzas:
        fields = dict(line.split(": ", 1) for line in stanza.splitlines() if ": " in line)
        if fields.get("Package") == name:
            states.add(fields.get("Status", "").rpartition(" ")[2])
    return states


def dpkg_installed(name, root):
    """Whether dpkg's database has NAME installed now."""
    return "installed" in dpkg_states(name, root)


def dpkg_ever_installed(name, root):
    """Whether dpkg's logs record NAME installed at some point, removed later or not."""
    import gzip
    marker = f" status installed {name}:"
    for log in sorted((root / "var/log").glob("dpkg.log*")):
        try:
            data = gzip.decompress(log.read_bytes()) if log.suffix == ".gz" else log.read_bytes()
        except (OSError, EOFError, gzip.BadGzipFile):
            continue
        if marker in data.decode(errors="replace"):
            return True
    return False


def desktop(settings, root=Path("/"), run=subprocess.run):
    """Install the desktop GunyahVM promised this guest, at every boot until it has once.

    cloud-init installs packages only on an instance's first boot, so a network
    that was down then left the guest without a desktop for good. GunyahVM
    records what it has delivered in DESKTOP_STATE and installs the rest; what
    the user removes later is never installed again. A guest created before
    this record existed counts as delivered when dpkg has, or once had, the
    desktop installed.
    """
    wanted = [p for p in settings.get("desktop", []) if re.fullmatch(r"[a-z0-9][a-z0-9+.-]+", p)]
    if len(wanted) != len(settings.get("desktop", [])):
        raise ValueError("invalid desktop package name")
    path = root / DESKTOP_STATE
    try:
        recorded = path.read_text()
        delivered = json.loads(recorded)["delivered"]
        if not isinstance(delivered, list) or not all(isinstance(p, str) for p in delivered):
            raise ValueError("invalid desktop record")
    except (OSError, ValueError, KeyError, TypeError):
        recorded = None
        if not wanted:
            return {"status": "none", "problems": []}
        earlier = dpkg_installed(DESKTOP_ANCHOR, root) or dpkg_ever_installed(DESKTOP_ANCHOR, root)
        delivered = list(wanted) if earlier else []
    # A package dpkg has unpacked but not configured is not delivered, whatever the record or the
    # logs say: the steps below finish it.
    pending = [p for p in wanted if p not in delivered or dpkg_states(p, root) & UNFINISHED]

    def save():
        content = json.dumps({"delivered": delivered}) + "\n"
        if content == recorded:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_name(path.name + ".part")
        part.write_text(content)
        os.replace(part, path)

    if not pending:
        save()
        return {"status": "done", "problems": []}
    env = {**os.environ, **APT_ENV}

    def apt(argv):
        result = run(argv, capture_output=True, text=True, env=env, check=False)
        sys.stderr.write((result.stdout or "") + (result.stderr or ""))
        return result

    # A boot that stopped during an earlier attempt leaves dpkg to finish.
    apt(["dpkg", *DPKG_CONFFILES, "--configure", "-a"])
    # apt-daily and unattended-upgrades also run on a fresh image's first
    # boot; wait for the lock they hold instead of failing on it.
    wait = ["-o", "DPkg::Lock::Timeout=3600"]
    update = apt(["apt-get", "-q", *wait, "update"])
    options = [arg for option in DPKG_CONFFILES for arg in ("-o", "Dpkg::Options::=" + option)]
    result = apt(["apt-get", "-q", "-y", *wait, *options, "install", *pending])
    if result.returncode:
        lines = [line.strip() for line in ((update.stdout or "") + (update.stderr or "")
                                           + (result.stdout or "") + (result.stderr or "")).splitlines()
                 if line.startswith(("E:", "Err:", "W:"))]
        detail = " / ".join(lines[-3:]) or f"apt-get exit {result.returncode}"
        save()
        return {"status": "error", "problems": [
            "The desktop could not be installed; GunyahVM tries again at the next start: " + detail]}
    delivered += [p for p in pending if p not in delivered]
    save()
    return {"status": "installed", "problems": [], "installed": pending}


def setup(seed, root=Path("/")):
    def installed(path):
        return root / path.lstrip("/")

    settings = json.loads((seed / "runtime.json").read_text())
    align_network_wait(root)
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
                select_user_session(root, username, settings["mode"])
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


def install_desktop(mount):
    """The --desktop step: install what is due, then show the new desktop."""
    result = desktop(json.loads((mount / "runtime.json").read_text()))
    if result["status"] == "installed":
        # Autologin and the graphics session need the display manager that
        # was just installed; the setup of this boot ran before it existed.
        setup(mount)
        subprocess.run(["systemctl", "daemon-reload"], check=False)
        subprocess.run(["systemctl", "--no-block", "restart", "display-manager.service"],
                       check=False)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    step = install_desktop if sys.argv[1:] == ["--desktop"] else setup
    mount = Path(tempfile.mkdtemp(prefix="gunyahvm-seed.", dir="/run"))
    subprocess.run(
        ["mount", "-t", "iso9660", "-o", "ro", "/dev/disk/by-label/cidata", str(mount)],
        check=True,
    )
    try:
        step(mount)
    finally:
        subprocess.run(["umount", str(mount)], check=True)
        mount.rmdir()
