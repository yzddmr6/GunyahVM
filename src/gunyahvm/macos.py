# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""macOS guest profile: locally assembled inputs, ordinary-user VMM and control.

Apple's files are downloaded on the device and assembled there
(macos_create.py); the package carries only GunyahVM's own boot kit, VMM and
renderer. Boot geometry is sealed at assembly; changing RAM, CPUs or virtual
devices requires a new boot disk.
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
    """What macOS needs from the running kernel, probed, never read from its version.

    The kernel must share VM-fd memory at runtime (GH_CAP_PROTECTED_SHARE);
    the GPU profile needs an accessible msm render node and /dev/udmabuf, which
    the VMM uses for guest memory blobs. The device releases that first ran
    macOS (Xiaomi 1.3.1, OnePlus 0.9.1) also brought two msm fixes for heavy
    guest graphics: virtually contiguous submit bookkeeping and write-back
    mappings for udmabuf imports. A kernel without them still boots macOS;
    userspace cannot see them, so they are not a gate.
    """
    validate_config(cfg)
    runtime = snapshot.runtime.get("components", {}).get("macos", {})
    if not {"vm-fd-share", "virtio-mmio", "simplefb-wayland"} <= set(runtime.get("features", [])):
        raise ValueError("macOS runtime is not installed")
    if snapshot.protected_share is not True:
        raise ValueError("macOS needs kernel VM-fd runtime sharing")
    if cfg["macos"]["gpu"]:
        if not any(n.get("driver") == "msm" and n.get("accessible") for n in snapshot.render_nodes):
            raise ValueError("macOS GPU profile requires an accessible MSM render node")
        if not snapshot.udmabuf:
            raise ValueError("macOS GPU profile requires read/write access to /dev/udmabuf (CONFIG_UDMABUF)")
    if cfg["vhost_net"]:
        raise ValueError("This macOS profile uses userspace virtio networking")


def import_bundle(store, name, bundle):
    """Import under a library lock; never duplicate a control identity there."""
    store = Path(store).expanduser().resolve()
    if not store.is_relative_to(Path.home().resolve()):
        raise ValueError("VM storage must be inside the owner home directory")
    if os.geteuid() == 0:
        raise ValueError("Import as the ordinary VM owner")
    store.mkdir(mode=0o700, parents=True, exist_ok=True)
    with storage.operation_lock(store):
        return _import_bundle(store, name, bundle)


def _import_bundle(store, name, bundle):
    """Import a locally assembled, hash-sealed profile, without privileged reads."""
    from .core import library_subnets, vm_name
    name = vm_name(name)
    if os.geteuid() == 0:
        raise ValueError("Import as the ordinary VM owner")
    store = Path(store).expanduser().resolve()
    if not store.is_relative_to(Path.home().resolve()):
        raise ValueError("VM storage must be inside the owner home directory")
    bundle = Path(bundle).resolve()
    manifest = json.loads((bundle / "inputs.json").read_text())
    inputs = INPUTS + (("firmware.fd",) if manifest.get("schema") == 3 else ())
    if manifest.get("schema") not in (2, 3) or set(manifest.get("files", {})) != set(inputs):
        raise ValueError("Invalid macOS input manifest")
    ident = str(uuid.uuid4())
    vm = store / "vms" / name
    cfg = config.complete_new({
        "id": ident, "uid": os.getuid(), "gid": os.getgid(), "name": name,
        "os": "macos", "username": "root", "cpus": manifest["boot"]["cpus"],
        "memory_mib": manifest["boot"]["memory_mib"], "disk_gib": manifest.get("disk_gib", 48),
        "memory_mode": "shared", "boot_managed": False, "guest_payload": False,
        "vhost_net": False, "install_media": False, "macos": manifest["boot"],
        "subnet": config.free_subnet(ident, library_subnets(store)),
        "source_image": "Local macOS boot bundle", "ssh_identity": str(vm / "agent-key"),
    })
    cfg["display"].update(gpu="simplefb", fullscreen=True, width=1280, height=800, vnc=False)
    config.validate(cfg)
    for name in inputs:
        p = bundle / name
        if p.is_symlink() or not p.is_file():
            raise ValueError("Invalid bundle input: " + name)
        expected = manifest["files"][name]
        if not isinstance(expected, str) or not re.fullmatch("[a-f0-9]{64}", expected):
            raise ValueError("Missing input digest: " + name)
        with p.open("rb") as f:
            if hashlib.file_digest(f, "sha256").hexdigest() != expected:
                raise ValueError("Input digest mismatch: " + name)
    if vm.exists() or vm.is_symlink():
        raise FileExistsError(vm)
    public = control_public_key(bundle / "agent-key")
    for existing in (store / "vms").glob("*/agent-key"):
        if control_public_key(existing) == public:
            raise ValueError("This control key already belongs to a VM; provision a new key in the new guest before importing")
    vm.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    vm.mkdir(mode=0o700)  # Existing user data is never replaced.
    try:
        for name in inputs:
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
    from .crosvmargs import display_refresh
    hz = display_refresh(cfg["display"].get("output", ""))
    a += ["--simplefb", f"width=1280,height=800,poll-hz={hz},wayland-socket={wayland}",
          "--display-window-keyboard", "--display-window-mouse", str(local_firmware(vm, files))]
    return a, {**components.library_environment(snapshot.runtime, "macos"),
               "CROSVM_GVM_FULLSCREEN": "1"}


def unwanted_boot_arg(token):
    """Kit boot arguments a run leaves out.

    wfi=0 kept idle vCPUs spinning (volumes made by 1.3.0). stderr-log is a
    bring-up diagnostic of the XNU patches that copies everything every
    process writes to stdout and stderr into the kernel log; nothing reads
    that log.
    """
    return token == "wfi=0" or token.startswith("stderr-log=")


def boot_args(args):
    return " ".join(token for token in args.split() if not unwanted_boot_arg(token))


def boot_image_for_run(source, run_dir):
    """Serve corrected UniXNU args from a private copy of an older boot volume."""
    entry = "::/EFI/UniXNU/unixnu.toml"
    content = subprocess.check_output(["mcopy", "-i", source, entry, "-"])
    def correct(match):
        args = match[1].decode()
        if not any(unwanted_boot_arg(token) for token in args.split()):
            return match[0]
        return b'args = "' + boot_args(args).encode() + b'"'

    updated = re.sub(rb'^args = "([^"\r\n]*)"', correct, content, flags=re.MULTILINE)
    if updated == content:
        return source
    target = run_dir / "boot.img"
    subprocess.run(["cp", "--reflink=auto", "--sparse=always", source, target], check=True)
    subprocess.run(["mcopy", "-o", "-i", target, "-", entry], input=updated, check=True)
    return target


# The supervisor records these in English, like every other failure; the VM
# page explains them in the user's language (ui.text.EXPLANATIONS), whatever
# language the VM was started in.
BOOT_MEMORY_REFUSED = (
    "Gunyah could not map the virtual machine's boot memory. Shut down other virtual machines "
    "or close applications to free contiguous host RAM, then start the VM again.")
BOOT_MEMORY_UNAVAILABLE = (
    "The host could not allocate and lock the virtual machine's boot memory. "
    "See crosvm.stderr for the allocation error.")


def boot_memory_error(log):
    """Return the VMM's admission failure from the end of this run's log."""
    with log.open("rb") as stream:
        stream.seek(0, os.SEEK_END)
        stream.seek(max(0, stream.tell() - 8192))
        tail = stream.read()
    if b"GH-SHIM: gave up: the resource manager refused MEM_ACCEPT (error 0x9)" in tail:
        return BOOT_MEMORY_REFUSED
    if b"Gunyah boot RAM preparation failed:" in tail:
        return BOOT_MEMORY_UNAVAILABLE
    return None


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


def local_firmware(vm, files):
    """A VM's own firmware.fd, else the boot kit's, else one kept by an upgrade."""
    candidates = [Path(vm) / "firmware.fd"]
    if files.get("firmware"):
        candidates.append(Path(files["firmware"]))
    candidates.append(Path("/var/lib/gunyahvm/local-macos/firmware.fd"))
    for candidate in candidates:
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise ValueError("The macOS firmware is missing; reinstall GunyahVM")


def control_public_key(path):
    result = subprocess.run(["ssh-keygen", "-y", "-P", "", "-f", str(path)],
                            capture_output=True, text=True, check=True, timeout=10)
    parts = result.stdout.split()
    if len(parts) < 2:
        raise ValueError("Invalid control key")
    return " ".join(parts[:2])


def create_control_identity(directory):
    """Create a new local identity for an assembler to install into its Data seed.

    This does not rotate an existing guest or claim to provision its APFS disk.
    The assembler must put agent-key.pub in the new guest's authorized_keys.
    """
    directory = Path(directory)
    directory.mkdir(mode=0o700, parents=False, exist_ok=False)
    try:
        key = directory / "agent-key"
        subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "",
                        "-C", "gunyahvm-" + str(uuid.uuid4()), "-f", str(key)],
                       check=True, capture_output=True, timeout=30)
        key.chmod(0o600)
        (directory / "agent-key.pub").chmod(0o600)
        return key
    except BaseException:
        shutil.rmtree(directory)
        raise
