# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Ordinary-user cloud-init media generation."""

import json
import subprocess
import shutil
from . import components
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent

# cloud-init runs bootcmd on every boot, before sysinit.target, with the seed of
# the GunyahVM version that started the VM; write_files runs once per instance.
REFRESH = (
    'd=$(mktemp -d /run/gunyahvm-seed.XXXXXX) || exit 0; '
    'if mount -o ro,nosuid,nodev -L cidata "$d"; then '
    'python3 "$d/gunyahvm-refresh.py" "$d"; r=$?; umount "$d"; else r=0; fi; '
    'rmdir "$d"; exit $r'
)


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
                # crosvm's device tree has no model, so flash-kernel (run by every initramfs
                # update on Ubuntu arm64) reports "Unsupported platform ''" and fails the package
                # transaction. GunyahVM boots the guest kernel directly; name the machine as the
                # generic virtual machine flash-kernel already knows, for which it does nothing.
                "path": "/etc/flash-kernel/machine",
                "permissions": "0644",
                "owner": "root:root",
                "content": "linux,dummy-virt\n",
            },
            {
                "path": "/etc/systemd/system/display-manager.service.d/gunyahvm-graphics.conf",
                "permissions": "0644",
                "owner": "root:root",
                "content": "[Service]\nEnvironment=VIRGL_DEBUG=nocoherent\n",
            },
            {
                "path": "/usr/local/bin/gunyahvm-graphics-run",
                "permissions": "0755",
                "owner": "root:root",
                "content": (BUNDLE / "guest-graphics-run").read_text(),
            },
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
            -1, ["systemctl", "--no-block", "restart", "display-manager.service"]
        )
    settings = {
        "mode": cfg["display"]["gpu"],
        "username": cfg["username"],
        "shared_folders": [{k: s[k] for k in ("tag", "backend", "ro")}
                           for s in cfg["shares"] if s.get("backend") == "virtiofs"],
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
    # The same GunyahVM-owned files also travel on the seed, so a VM created by an
    # older version receives this version's guest scripts on its next boot.
    guest_files = []
    for item in user["write_files"]:
        name = item["path"].strip("/").replace("/", "_")
        (directory / name).write_text(item["content"])
        guest_files.append(
            {"source": name, "path": item["path"], "permissions": item["permissions"]}
        )
        runtime_files.append(directory / name)
    (directory / "guest-files.json").write_text(json.dumps(guest_files, indent=2) + "\n")
    shutil.copyfile(BUNDLE / "guest-refresh.py", directory / "gunyahvm-refresh.py")
    runtime_files += [directory / "guest-files.json", directory / "gunyahvm-refresh.py"]
    user["bootcmd"] = [["sh", "-c", REFRESH]]
    if cfg["display"]["gpu"] != "none" and cfg["guest_distribution"] == "ubuntu":
        user["packages"] = [
            "ubuntu-desktop-minimal",
            "mesa-utils",
            "mesa-vulkan-drivers",
            "vulkan-tools",
            "wl-clipboard",
        ]
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
                # netplan's early generator may otherwise resolve the MAC to
                # eth0 before udev renames it, leaving wait-online on a stale name.
                "set-name": "vmnet0",
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
