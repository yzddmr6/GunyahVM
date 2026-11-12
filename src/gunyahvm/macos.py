# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""macOS guest profile: locally assembled inputs, ordinary-user VMM and control.

Apple images are always user inputs. The installed runtime contains only the
community VMM, firmware and renderer. Boot geometry is sealed by the assembler;
changing RAM, CPUs or virtual devices requires a new boot bundle.
"""

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

from . import components, config, storage, windows_agent

INPUTS = ("boot.img", "disk.raw", "agent-key")


def validate_config(cfg):
    m = cfg.get("macos", {})
    if not isinstance(m, dict) or set(m) != {"profile", "layout", "memory_mib", "cpus", "gpu", "agent_port"}:
        raise ValueError("Invalid macOS boot profile")
    if m["profile"] != "27.2" or type(m["gpu"]) is not bool or m["agent_port"] != 2222:
        raise ValueError("Unsupported macOS profile")
    if m["layout"] != "native-apfs-v2":
        raise ValueError("macOS requires the native APFS and separate physmap boot layout")
    if type(m["memory_mib"]) is not int or not 8192 <= m["memory_mib"] <= 16384:
        raise ValueError("This macOS boot layout requires 8192..16384 MiB")
    if type(m["cpus"]) is not int or not 6 <= m["cpus"] <= 8:
        raise ValueError("macOS requires 6..8 vCPUs")
    if (cfg["memory_mib"], cfg["cpus"]) != (m["memory_mib"], m["cpus"]):
        raise ValueError("Reassemble the macOS boot bundle when RAM or CPUs change")
    if cfg["memory_mode"] != "shared" or cfg["guest_payload"] or cfg.get("boot_managed"):
        raise ValueError("macOS uses its own boot bundle and VM-fd shared memory")
    if cfg["display"]["gpu"] != "simplefb" or cfg["display"]["vnc"] or cfg["shares"]:
        raise ValueError("macOS requires embedded simplefb; 9p sharing is unavailable")
    if (cfg["display"]["width"], cfg["display"]["height"]) != (1280, 800):
        raise ValueError("Reassemble macOS boot geometry to change guest resolution")


def require(snapshot, cfg):
    validate_config(cfg)
    runtime = snapshot.runtime.get("components", {}).get("macos", {})
    if not {"vm-fd-share", "virtio-mmio", "simplefb-wayland"} <= set(runtime.get("features", [])):
        raise ValueError("macOS runtime is not installed")
    if snapshot.protected_share is not True:
        raise ValueError("macOS needs kernel VM-fd runtime sharing")
    if cfg["macos"]["gpu"] and not any(n.get("driver") == "msm" and n.get("accessible") for n in snapshot.render_nodes):
        raise ValueError("macOS GPU profile requires an accessible MSM render node")
    if cfg["vhost_net"]:
        raise ValueError("This macOS profile uses userspace virtio networking")


def import_bundle(store, name, bundle):
    """Import a locally assembled, hash-sealed profile, without privileged reads."""
    from .core import vm_name
    name = vm_name(name)
    if os.geteuid() == 0:
        raise ValueError("Import as the ordinary VM owner")
    store = Path(store).expanduser().resolve()
    if not store.is_relative_to(Path.home().resolve()):
        raise ValueError("VM storage must be inside the owner home directory")
    bundle = Path(bundle).resolve()
    manifest = json.loads((bundle / "inputs.json").read_text())
    if manifest.get("schema") != 2 or set(manifest.get("files", {})) != set(INPUTS):
        raise ValueError("Invalid macOS input manifest")
    ident = str(uuid.uuid4())
    vm = store / "vms" / name
    cfg = config.complete_new({
        "id": ident, "uid": os.getuid(), "gid": os.getgid(), "name": name,
        "os": "macos", "username": "root", "cpus": manifest["boot"]["cpus"],
        "memory_mib": manifest["boot"]["memory_mib"], "disk_gib": manifest.get("disk_gib", 48),
        "memory_mode": "shared", "boot_managed": False, "guest_payload": False,
        "vhost_net": False, "install_media": False, "macos": manifest["boot"],
        "subnet": f"10.203.{int(ident[:8], 16) % 250 + 1}.0/24",
        "source_image": "Local macOS boot bundle", "ssh_identity": str(vm / "agent-key"),
    })
    cfg["display"].update(gpu="simplefb", fullscreen=True, width=1280, height=800, vnc=False)
    config.validate(cfg)
    for name in INPUTS:
        p = bundle / name
        if p.is_symlink() or not p.is_file():
            raise ValueError("Invalid bundle input: " + name)
        expected = manifest["files"][name]
        if not isinstance(expected, str) or not re.fullmatch("[a-f0-9]{64}", expected):
            raise ValueError("Missing input digest: " + name)
        with p.open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != expected:
                raise ValueError("Input digest mismatch: " + name)
    vm.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    vm.mkdir(mode=0o700)  # Existing user data is never replaced.
    try:
        for name in INPUTS:
            subprocess.run(["cp", "--reflink=auto", "--sparse=always", "--", str(bundle / name), str(vm / name)], check=True)
            (vm / name).chmod(0o600)
        storage.atomic_json(vm / "inputs.json", manifest)
        storage.atomic_json(vm / "config.json", cfg)
        storage.atomic_json(vm / "state.json", {"state": "created", "clean_shutdown": None})
    except BaseException:
        shutil.rmtree(vm)
        raise
    return vm


def build(vm, cfg, rd, snapshot, wayland):
    require(snapshot, cfg)
    if not wayland:
        raise ValueError("macOS requires the application's display socket")
    if any(c in str(p) for p in (vm, rd, wayland) for c in ",:\n\0"):
        raise ValueError("VM paths may not contain crosvm parameter separators")
    files = snapshot.runtime["components"]["macos"]["files"]
    a = [files["binary"], "--no-syslog", "--log-level", "info", "--extended-status", "run",
         "--hypervisor", "gunyah", "--mem", str(cfg["memory_mib"]), "--cpus", str(cfg["cpus"]),
         "--protected-vm-pseudo-unprotected", "--disable-sandbox", "--hugepages",
         "--prepare-lend-mthp-mode", "single", "--pre-alloc", "drm-host-mb=8",
         "--socket", str(rd / "control.sock"),
         "--serial", f"type=file,hardware=serial,num=1,path={rd}/serial.log",
         "--dump-device-tree-blob", str(rd / "generated.dtb"),
         "--block", str(vm / "boot.img") + ",ro=true"]
    if cfg["macos"]["gpu"]:
        a += ["--gpu", "backend=virglrenderer,context-types=drm,egl=true,glx=false,surfaceless=true,udmabuf=true,external-blob=true,fixed-blob-mapping=false,width=1280,height=800", "--virtio-mmio-gpu"]
    a += ["--virtio-mmio-block", str(vm / "disk.raw")]
    if cfg["network_mode"] == "nat":
        a += ["--net", f"tap-name={cfg['tap']},mac={cfg['mac']}", "--virtio-mmio-net"]
    a += ["--simplefb", "width=1280,height=800,wayland-socket=" + str(wayland),
          "--display-window-keyboard", "--display-window-mouse", files["firmware"]]
    return a, {**components.library_environment(snapshot.runtime, "macos"),
               "CROSVM_GVM_FULLSCREEN": "1"}


def command(vm, cfg, ip, cmd):
    ipaddress.ip_address(ip)
    argv = windows_agent.win_ssh_argv([vm / "agent-key"], "root", ip, vm / "known_hosts", cmd)
    argv[1:1] = ["-p", str(cfg["macos"]["agent_port"])]
    return subprocess.run(argv, capture_output=True, text=True, timeout=15)


def poweroff(vm, cfg, ip):
    if not ip:
        raise ValueError("macOS has no authenticated control address")
    result = command(vm, cfg, ip, "/sbin/shutdown -h now")
    if result.returncode:
        raise ValueError("macOS refused clean shutdown: " + result.stderr.strip())
    return True
