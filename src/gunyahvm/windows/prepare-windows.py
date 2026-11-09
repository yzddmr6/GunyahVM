#!/usr/bin/python3
# SPDX-License-Identifier: MIT
# Copyright (C) 2026 yzddMr6
"""Prepare owner-only Windows ARM64 DISM media without mounts or privilege."""

import argparse
import getpass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import uuid
from xml.etree import ElementTree as ET

from windows_deploy import write_payload

MIB = 1024**2
GIB = 1024**3
TOOLS = (
    "7z",
    "wimlib-imagex",
    "sfdisk",
    "mkfs.vfat",
    "mcopy",
    "mkntfs",
    "ntfscp",
    "ntfsls",
    "fallocate",
)


def run(argv, **kwargs):
    return subprocess.run([str(x) for x in argv], check=True, **kwargs)


def space(path, remaining=0):
    if shutil.disk_usage(path).free < remaining + 8 * GIB:
        raise RuntimeError(
            "Insufficient free space for Windows media plus an 8 GiB reserve"
        )


def iso_wim(iso):
    listing = subprocess.check_output(["7z", "l", "-slt", str(iso)], text=True)
    entries = []
    for block in listing.split("\n\n"):
        values = dict(
            line.split(" = ", 1) for line in block.splitlines() if " = " in line
        )
        if values.get("Path", "").replace("\\", "/").lower() == "sources/install.wim":
            entries.append((values["Path"], int(values["Size"])))
    if len(entries) != 1:
        raise ValueError(
            "Use an official Windows ARM64 ISO containing sources/install.wim"
        )
    return entries[0]


class WimImages:
    """Read only the bounded, uncompressed XML resource in the WIM stream.

    Offsets are from wimlib's public wim_header_disk / wim_reshdr_disk format.
    DISM and wimlib own all image application and modification.
    """

    def __init__(self):
        self.position = 0
        self.offset = None
        self.size = None
        self.xml = bytearray()

    def accept(self, data):
        if self.position == 0:
            if len(data) < 208 or data[:8] != b"MSWIM\0\0\0":
                raise ValueError("Not a standard WIM")
            if struct.unpack_from("<I", data, 8)[0] != 208:
                raise ValueError("Unsupported WIM header")
            if struct.unpack_from("<HH", data, 40) != (1, 1):
                raise ValueError("Split WIM images are not supported")
            packed, self.offset, original = struct.unpack_from("<QQQ", data, 72)
            self.size = packed & ((1 << 56) - 1)
            if (
                packed >> 56 & 4
                or not 0 < self.size <= 16 * MIB
                or original != self.size
            ):
                raise ValueError("Unsupported WIM XML resource")
            if self.offset < 208:
                raise ValueError("Invalid WIM XML offset")
        start = max(self.position, self.offset)
        end = min(self.position + len(data), self.offset + self.size)
        if end > start:
            self.xml.extend(data[start - self.position : end - self.position])
        self.position += len(data)

    def images(self):
        if len(self.xml) != self.size:
            raise ValueError("Incomplete WIM metadata")
        root = ET.fromstring(self.xml)
        images = []
        for image in root.findall("IMAGE"):
            if image.findtext("WINDOWS/ARCH") != "12":
                continue
            images.append(
                {
                    "index": int(image.attrib["INDEX"]),
                    "name": image.findtext("NAME"),
                    "edition": image.findtext("WINDOWS/EDITIONID"),
                    "build": image.findtext("WINDOWS/VERSION/BUILD"),
                }
            )
        if not images:
            raise ValueError("The WIM contains no ARM64 Windows editions")
        return images


def install_disk(iso, image):
    member, expected = iso_wim(iso)
    size = ((expected + 512 * MIB + GIB - 1) // GIB) * GIB
    space(image.parent, expected + 1400 * MIB)
    with image.open("xb") as stream:
        stream.truncate(size)
    # mkntfs/ntfscp have no partition-offset option. Format a regular volume,
    # then prepend GPT space by moving extents in place, without a second copy.
    run(
        [
            "mkntfs",
            "-Q",
            "-F",
            "-s",
            "512",
            "-p",
            "2048",
            "-L",
            "GUNYAHVMINSTALL",
            image,
        ],
        stdout=subprocess.DEVNULL,
    )
    reader = writer = None
    metadata = WimImages()
    try:
        writer = subprocess.Popen(
            ["ntfscp", "-f", str(image), "/proc/self/fd/0", "/install.wim"],
            stdin=subprocess.PIPE,
        )
        reader = subprocess.Popen(
            ["7z", "x", "-so", str(iso), member], stdout=subprocess.PIPE
        )
        with writer.stdin as target:
            while data := reader.stdout.read(4 * MIB):
                metadata.accept(data)
                target.write(data)
                space(image.parent)
        reader.stdout.close()
        if reader.wait() or writer.wait() or metadata.position != expected:
            raise RuntimeError("ISO extraction or NTFS transfer failed")
        listing = subprocess.check_output(["ntfsls", "-l", str(image)], text=True)
        sizes = [
            int(line.split()[0])
            for line in listing.splitlines()
            if line.split() and line.split()[-1] == "install.wim"
        ]
        if sizes != [expected]:
            raise RuntimeError("NTFS install.wim size does not match the ISO")
    finally:
        for proc in (reader, writer):
            if proc and proc.poll() is None:
                proc.terminate()
                proc.wait()
    images = metadata.images()
    try:
        run(
            [
                "fallocate",
                "--insert-range",
                "--offset",
                "0",
                "--length",
                str(MIB),
                image,
            ]
        )
    except subprocess.CalledProcessError as error:
        raise RuntimeError(
            "VM storage must support insert-range (use ext4 or XFS)"
        ) from error
    with image.open("r+b") as stream:
        stream.truncate(size + 2 * MIB)
    run(
        ["sfdisk", "--wipe", "never", "--wipe-partitions", "never", image],
        input=f"label: gpt\nstart=2048,size={size // 512},type=EBD0A0A2-B9E5-4433-87C0-68B6B72699C7\n",
        text=True,
        stdout=subprocess.DEVNULL,
    )
    return images


def choose(images, requested):
    for item in images:
        print(
            f"{item['index']}: {item['name']} (ARM64, build {item['build']})",
            file=sys.stderr,
        )
    if requested is None:
        if not sys.stdin.isatty():
            raise ValueError(
                "Choose an edition explicitly with --edition (name or listed index)"
            )
        print("Windows edition: ", end="", file=sys.stderr, flush=True)
        requested = input().strip()
    matches = [x for x in images if requested in (str(x["index"]), x["name"], x.get("edition"))]
    if len(matches) != 1:
        raise ValueError("Edition must match one of the WIM entries above")
    return matches[0]


def fat_disk(image, media, size, label, esp=False):
    if not 1 <= len(label.encode("ascii")) <= 11:
        raise ValueError("FAT volume label must contain 1 to 11 ASCII bytes")
    with image.open("xb") as stream:
        stream.truncate(size)
    sectors = (size - 2 * MIB) // 512
    kind = "U" if esp else "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
    run(
        ["sfdisk", image],
        input=f"label: gpt\nstart=2048,size={sectors},type={kind}\n",
        text=True,
        stdout=subprocess.DEVNULL,
    )
    run(
        [
            "mkfs.vfat",
            "-F",
            "32",
            "-h",
            "2048",
            "-n",
            label,
            "--offset",
            "2048",
            image,
            str(sectors // 2),
        ],
        stdout=subprocess.DEVNULL,
    )
    for child in media.iterdir():
        run(["mcopy", "-s", "-i", str(image) + "@@1048576", child, "::/"])


def verify_openssh(assets):
    """Verify local media inputs before expensive ISO processing; never fetch."""
    try:
        spec = json.loads((assets / "openssh.json").read_text())
        if (
            spec.get("filename") != "OpenSSH-ARM64.zip"
            or spec.get("architecture") != "arm64"
            or not re.fullmatch(r"[0-9a-f]{64}", spec.get("sha256", ""))
        ):
            raise ValueError("Invalid offline OpenSSH manifest")
        archive = assets / spec["filename"]
        with archive.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != spec["sha256"]:
                raise ValueError("Offline OpenSSH SHA256 mismatch; reinstall GunyahVM")
    except FileNotFoundError as error:
        raise ValueError("Missing offline OpenSSH payload; reinstall GunyahVM") from error


def copy_openssh(assets, payload):
    verify_openssh(assets)
    for name in ("OpenSSH-ARM64.zip", "openssh.json"):
        shutil.copyfile(assets / name, payload / name)
    verify_openssh(payload)
    for name in ("agent-setup.ps1", "install-openssh.ps1"):
        shutil.copyfile(Path(__file__).with_name(name), payload / name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iso", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--edition")
    parser.add_argument("--local-user")
    parser.add_argument("--password-fd", type=int)
    parser.add_argument("--agent-config", type=Path)
    parser.add_argument("--instance-id", default=None)
    parser.add_argument(
        "--assets", type=Path, default=Path("/usr/share/gunyahvm/windows")
    )
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise ValueError("Prepare Windows media as its ordinary owner")
    if args.local_user and args.password_fd is None and not sys.stdin.isatty():
        raise ValueError(
            "--local-user requires an interactive terminal for password entry"
        )
    missing = [name for name in TOOLS if not shutil.which(name)]
    if missing:
        raise ValueError("Install gunyahvm; missing tools: " + ", ".join(missing))
    try:
        import hivex  # noqa: F401 -- report the optional dependency before writing media
    except ImportError as error:
        raise ValueError("Install gunyahvm (python3-hivex is required)") from error
    if args.agent_config:
        verify_openssh(args.assets)
    os.umask(0o077)
    os.setpriority(os.PRIO_PROCESS, 0, max(10, os.getpriority(os.PRIO_PROCESS, 0)))
    token = str(uuid.UUID(args.instance_id)) if args.instance_id else str(uuid.uuid4())
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=False)
    try:
        print("Preparing installation data without mounts...", file=sys.stderr)
        images = install_disk(args.iso.resolve(), out / "install.raw")
        edition = choose(images, args.edition)
        account = None
        if args.local_user:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,19}", args.local_user):
                raise ValueError(
                    "--local-user: use 1..20 ASCII letters, digits, dot, underscore or hyphen"
                )
            if args.local_user.lower() in (
                "administrator",
                "guest",
                "defaultaccount",
                "wdagutilityaccount",
            ):
                raise ValueError("Choose a non-reserved Windows local account name")
            if args.password_fd is not None:
                password = os.read(args.password_fd, 4096).decode().rstrip("\r\n")
            else:
                password = getpass.getpass(
                    "Windows local password (hidden, not encrypted in the answer file): "
                )
                if password != getpass.getpass("Repeat Windows local password: "):
                    raise ValueError("Passwords must match")
            if not password or len(password) > 256:
                raise ValueError("Password must contain 1..256 characters")
            account = {"username": args.local_user, "password": password}
        with tempfile.TemporaryDirectory(prefix=".media-", dir=out) as temp:
            temp = Path(temp)
            media = temp / "media"
            media.mkdir()
            payload = temp / "payload"
            payload.mkdir()
            for name in ("rdmapool", "pvmpower", "viostor", "NetKVM", "vioinput"):
                shutil.copytree(
                    args.assets / "drivers" / name, payload / "drivers" / name
                )
            for name in ("DroidVM_Test.cer", "winpe-helper.exe"):
                shutil.copyfile(args.assets / name, payload / name)
            write_payload(
                payload, edition["index"], account, token, agent=bool(args.agent_config)
            )
            if args.agent_config:
                settings = json.loads(args.agent_config.read_text())
                if set(settings) != {"user", "key", "gateway"} or not re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_.-]{0,31}", settings["user"]
                ):
                    raise ValueError("invalid Windows agent settings")
                import ipaddress

                ipaddress.IPv4Address(settings["gateway"])
                if not re.fullmatch(
                    r"ssh-ed25519 [A-Za-z0-9+/=]+(?: [^\r\n]*)?", settings["key"]
                ):
                    raise ValueError("invalid Windows agent public key")
                shutil.copyfile(args.agent_config, payload / "agent.json")
                copy_openssh(args.assets, payload)
            account = None
            password = None
            run(
                [
                    "7z",
                    "x",
                    "-y",
                    "-o" + str(media),
                    args.iso.resolve(),
                    "efi",
                    "boot",
                    "sources/boot.wim",
                    "bootmgr",
                    "bootmgr.efi",
                ],
                stdout=subprocess.DEVNULL,
            )
            run(
                [
                    sys.executable,
                    Path(__file__).with_name("patch-bcd.py"),
                    media / "efi/microsoft/boot/bcd",
                ],
                stdout=subprocess.DEVNULL,
            )
            shell = temp / "winpeshl.ini"
            shell.write_bytes(
                b"[LaunchApps]\r\n%SYSTEMROOT%\\System32\\cmd.exe, /c X:\\gunyahvm\\bootstrap.cmd\r\n"
            )
            commands = f"add {json.dumps(str(payload))} /gunyahvm\nadd {json.dumps(str(shell))} /Windows/System32/winpeshl.ini\n"
            run(
                [
                    "wimlib-imagex",
                    "update",
                    media / "sources/boot.wim",
                    "2",
                    "--threads=" + str(len(os.sched_getaffinity(0))),
                ],
                input=commands,
                text=True,
                stdout=subprocess.DEVNULL,
            )
            (media / "GUNYAHVMBOOT.TAG").write_text(token + "\n")
            fat_disk(out / "boot.raw", media, 1536 * MIB, "GUNYAHBOOT", esp=True)
            fat_disk(out / "drivers.raw", payload, 64 * MIB, "GUNYAHVMDRV")
        print(
            json.dumps(
                {"edition": edition, "instance_id": token, "install_media": True}
            )
        )
    except BaseException:
        shutil.rmtree(out)
        raise


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print("prepare-windows: " + str(error), file=sys.stderr)
        sys.exit(1)
