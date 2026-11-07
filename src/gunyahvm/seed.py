# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Ordinary-user cloud-init media generation."""

import json
import subprocess
import shutil
from . import components
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent


def run(argv):
    return subprocess.run([str(a) for a in argv], check=True)


def seed(vm, cfg, key):
    directory = vm / "seed"
    directory.mkdir(exist_ok=True)
    user = {
        "users": [
            {
                "name": cfg["username"],
                "groups": ["adm", "sudo"],
                "shell": "/bin/bash",
                "lock_passwd": True,
                "sudo": "ALL=(ALL) NOPASSWD:ALL",
                "ssh_authorized_keys": [key],
            }
        ],
        "ssh_pwauth": False,
        "disable_root": True,
        "write_files": [
            {
                "path": "/etc/systemd/system/gunyahvm-guest-setup.service",
                "permissions": "0644",
                "owner": "root:root",
                "content": (BUNDLE / "guest-setup.service").read_text(),
            },
            {
                "path": "/usr/local/libexec/gunyahvm-guest-setup",
                "permissions": "0755",
                "owner": "root:root",
                "content": (BUNDLE / "guest-setup.py").read_text(),
            },
            {
                "path": "/usr/local/sbin/gunyahvm-control",
                "permissions": "0755",
                "owner": "root:root",
                "content": (BUNDLE / "guest-control.py").read_text(),
            },
            {
                "path": "/etc/systemd/system/gunyahvm-control.service",
                "permissions": "0644",
                "owner": "root:root",
                "content": (BUNDLE / "guest-control.service").read_text(),
            },
        ],
        "runcmd": [
            ["systemctl", "daemon-reload"],
            ["systemctl", "mask", "--now", "serial-getty@hvc0.service"],
            [
                "systemctl",
                "enable",
                "gunyahvm-guest-setup.service",
                "gunyahvm-control.service",
            ],
            ["systemctl", "start", "gunyahvm-guest-setup.service"],
            ["systemctl", "start", "--no-block", "gunyahvm-control.service"],
        ],
    }
    if cfg["display"]["gpu"] != "none":
        user["runcmd"].insert(
            -1, ["systemctl", "try-restart", "display-manager.service"]
        )
    settings = {
        "mode": cfg["display"]["gpu"],
        "username": cfg["username"],
        "autologin": cfg["guest_distribution"] == "ubuntu"
        and cfg["display"]["gpu"] != "none",
    }
    runtime_files = []
    if cfg["guest_payload"]:
        runtime = components.load()
        kernel = runtime["components"]["guest_kernel"]
        settings.update(
            kernel_release=kernel["kernel_release"],
            guest_modules=kernel.get("modules_load", []),
        )
        for component, role, name in [
            ("guest_kernel", "modules", "guest-modules.tar"),
            ("guest_mesa", "archive", "guest-mesa.tar"),
        ]:
            if component == "guest_mesa" and settings["mode"] != "native":
                continue
            item = runtime["components"].get(component, {})
            if role in item.get("files", {}):
                shutil.copyfile(item["files"][role], directory / name)
                runtime_files.append(directory / name)
        if settings["mode"] == "native":
            settings["mesa"] = {
                k: runtime["components"]["guest_mesa"][k]
                for k in ("guest_abi", "library_subdir", "icd")
            }
    (directory / "runtime.json").write_text(json.dumps(settings) + "\n")
    if cfg["display"]["gpu"] != "none" and cfg["guest_distribution"] == "ubuntu":
        user["packages"] = ["ubuntu-desktop-minimal", "mesa-utils", "vulkan-tools"]
        user["package_update"] = True
    (directory / "user-data").write_text(
        "#cloud-config\n" + json.dumps(user, indent=2) + "\n"
    )
    (directory / "meta-data").write_text(
        json.dumps(
            {"instance-id": cfg["id"], "local-hostname": "gunyahvm-" + cfg["name"]}
        )
        + "\n"
    )
    network = {
        "version": 2,
        "ethernets": {
            "vmnet": {
                "match": {"macaddress": cfg["mac"]},
                "dhcp4": True,
                "dhcp6": False,
            }
        },
    }
    (directory / "network-config").write_text(json.dumps(network, indent=2) + "\n")
    run(
        [
            "genisoimage",
            "-quiet",
            "-output",
            vm / "seed.iso.part",
            "-volid",
            "cidata",
            "-joliet",
            "-rock",
            directory / "user-data",
            directory / "meta-data",
            directory / "network-config",
            directory / "runtime.json",
            *runtime_files,
        ]
    )
    (vm / "seed.iso.part").replace(vm / "seed.iso")
