# SPDX-License-Identifier: MIT
# Copyright (C) 2026 yzddMr6
"""Windows-specific media, display and boot arguments for the ordinary-user CLI."""

import json
import ipaddress
from pathlib import Path
import shutil
import socket
import subprocess
import sys
from . import components

ASSETS = Path("/usr/share/gunyahvm/windows")


def prepare(vm, cfg, args, bundle):
    script = bundle / "windows/prepare-windows.py"
    runtime = components.load()
    firmware = components.payload(runtime, "uefi", "image")
    assets = components.payload(runtime, "windows_drivers", "inventory").parent
    if not script.is_file() or not firmware.is_file():
        raise ValueError("Install gunyahvm before creating a Windows VM")
    if not args.iso or not args.iso.expanduser().is_file():
        raise ValueError("Windows requires --iso with your official ARM64 Windows ISO")
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(vm / "agent-key")],
        check=True,
    )
    agent = {
        "user": cfg["windows"]["ssh_user"],
        "key": (vm / "agent-key.pub").read_text().strip(),
        "gateway": str(ipaddress.ip_network(cfg["subnet"]).network_address + 1),
    }
    (vm / "agent.json").write_text(json.dumps(agent))
    command = [
        sys.executable,
        str(script),
        "--iso",
        str(args.iso.expanduser().resolve()),
        "--output",
        str(vm / "install"),
        "--instance-id",
        cfg["id"],
    ]
    command += ["--agent-config", str(vm / "agent.json"), "--assets", str(assets)]
    password_fd = getattr(args, "password_fd", None)
    if password_fd is not None:
        command += ["--password-fd", str(password_fd)]
    if args.edition:
        command += ["--edition", args.edition]
    if args.local_user:
        command += ["--local-user", args.local_user]
    # Preserve the terminal for edition selection and getpass; only stdout is JSON.
    prepared = subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        text=True,
        pass_fds=(password_fd,) if password_fd is not None else (),
    )
    cfg["windows_edition"] = json.loads(prepared.stdout)["edition"]
    shutil.copyfile(firmware, vm / "firmware.fd")
    with (vm / "disk.raw").open("xb") as stream:
        stream.truncate(cfg["disk_gib"] * 1024**3)


def display_available(cfg):
    # Match the VNC listener: SO_REUSEADDR lets a previous run's closing
    # connections be ignored, while an active listener still blocks bind().
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", cfg["vnc_port"]))
        except OSError as error:
            raise ValueError("Windows VNC loopback port is already in use") from error


def deployment_complete(vm, cfg):
    image = str(vm / "install/boot.raw") + "@@1048576"
    result = subprocess.run(
        ["mtype", "-i", image, "::/GUNYAHVM_DEPLOYED.TAG"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == cfg["id"]


def save_deployment_logs(vm):
    logs = vm / "deployment-logs"
    logs.mkdir(exist_ok=True)
    for name in (
        "partition.log",
        "dism-apply.log",
        "dism-drivers.log",
        "offline-drivers.log",
        "deployment-config.log",
    ):
        subprocess.run(
            [
                "mcopy",
                "-o",
                "-i",
                str(vm / "install/boot.raw") + "@@1048576",
                "::/" + name,
                str(logs / name),
            ],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def view(vm, cfg):
    if not shutil.which("remmina"):
        raise ValueError("Install gunyahvm (Remmina and its VNC plugin are required)")
    profile = vm / "display.remmina"
    profile.write_text(
        "[remmina]\nname=GunyahVM " + cfg["name"] + "\nprotocol=VNC\n"
        f"server=127.0.0.1:{cfg['vnc_port']}\nviewmode=1\n"
    )
    profile.chmod(0o600)
    subprocess.Popen(["remmina", "--connect", str(profile)], start_new_session=True)
