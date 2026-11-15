# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Assembly steps follow Droid-VM/macOS-DroidVM (make-macos-disk.py,
# stage-gpu-plugin.py, make-trust-bundle.py, run-macos-vm.py, k27/).
"""Create a macOS 27.2 VM from Apple's servers, on the tablet.

    fetch      only the IPSW members needed, from Apple's CDN (resumable)
    decode     Apple's kernel collection and trust caches; decrypt the system images
    link       the kit's kernel and board kexts into Apple's collection
    disk       an APFS container with System, Data, Preboot, VM, Update, Hardware
               and xART volumes, filled by a throwaway Linux helper VM
    stage      the account, Metal plugin and a new per-VM control key
    boot       the board's Apple device tree and the EFI boot disk
    publish    the finished VM appears in the library in one rename

Each step records its result in the work directory, so an interrupted or
cancelled creation resumes where it stopped. Nothing outside the work
directory and the new VM directory is written. No Apple file comes from
GunyahVM: the kit holds only the project's own binaries.
"""

import argparse
import contextlib
import ctypes
import hashlib
import io
import json
import os
from pathlib import Path
import plistlib
import re
import secrets
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import uuid

from . import macos_inputs, storage
from .macos_inputs import Cancelled

PROFILE = {"profile": "27.2", "layout": "native-apfs-v2", "memory_mib": 9216, "cpus": 6,
           "gpu": True, "agent_port": 2222}
KIT = Path("/usr/lib/gunyahvm/macos/kit")
PAGE = 4096
LABEL = b"DroidVMDataStore\x01\x00\x00\x00"
OTHER_VOLUMES = [("Preboot", "B"), ("VM", "V"), ("Update", "E"), ("Hardware", "H"), ("xART", "X")]
CRYPTEX_EXCLUDES = ["System/Library/dyld/dyld_shared_cache_x86_64*",
                    "System/Library/dyld/aot_shared_cache*"]
TEMPLATE = "System/Library/Templates/Data"
DSLOCAL = "./private/var/db/dslocal/nodes/Default"
RESERVED_USERS = {"root", "daemon", "nobody", "admin", "staff", "wheel", "guest", "everyone",
                  "operator", "sys", "tty", "kmem", "mail", "bin", "games", "news", "uucp"}
# Disk layout, data held by the finished VM, decrypt overlap, helper scratch.
DISK_LIMITS = (40, 512)
HELPER_MEMORY_MIB, HELPER_CPUS = 2048, 4
SETUP_SKIP_ITEMS = ["Accessibility", "AdditionalPrivacySettings", "Appearance", "AppleID",
                    "AppStore", "Biometric", "Diagnostics", "DisplayTone", "EnableLockdownMode",
                    "FileVault", "iCloudDiagnostics", "iCloudStorage", "Intelligence", "Location",
                    "OSShowcase", "Privacy", "Restore", "Safety", "ScreenTime", "Siri",
                    "SoftwareUpdate", "TermsOfAddress", "TextSize", "TOS", "TouchIDPrompt",
                    "UnlockWithWatch", "Wallpaper", "Welcome"]
DISABLED_JOBS = ["com.apple.apsd", "com.apple.AppSSODaemon", "com.apple.filesystems.fskitd"]
SSHD = ("mkdir -p /private/var/run/sshd; /usr/bin/ssh-keygen -A; exec /usr/sbin/sshd -D -p 2222 "
        "-o PermitRootLogin=prohibit-password -o PasswordAuthentication=no "
        "-o KbdInteractiveAuthentication=no -o UsePAM=no")


class Failed(ValueError):
    pass


def default_account():
    import pwd
    name = re.sub(r"[^a-z0-9_]", "", pwd.getpwuid(os.getuid()).pw_name.lower())
    return name if name and not name[0].isdigit() and name not in RESERVED_USERS else "user"


def valid_account(name):
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,30}", name or "") or name in RESERVED_USERS:
        raise Failed("macOS account names use lowercase letters, digits and _, and "
                     "cannot be a system name")
    return name


# --------------------------------------------------------------- kit -------

class Kit:
    def __init__(self, root=KIT):
        self.root = Path(root)
        path = self.root / "kit.json"
        if not path.is_file():
            raise Failed("The macOS boot kit is not installed; reinstall GunyahVM")
        self.manifest = json.loads(path.read_text())
        if self.manifest.get("schema") != 1:
            raise Failed("Unsupported macOS boot kit")

    def path(self, relative):
        return self.root / relative

    def verify(self):
        for relative, expected in self.manifest["files"].items():
            path = self.root / relative
            if path.is_symlink() or not path.is_file() or macos_inputs.digest(path) != expected:
                raise Failed("The macOS boot kit is damaged (" + relative + "); reinstall GunyahVM")


# ------------------------------------------------------------ decode -------

def _der(data, at):
    tag, size = data[at], data[at + 1]
    at += 2
    if size & 0x80:
        count = size & 0x7F
        size = int.from_bytes(data[at:at + count], "big")
        at += count
    if at + size > len(data):
        raise Failed("truncated Apple image")
    return tag, data[at:at + size], at + size


def im4p_payload(data, kind):
    tag, body, end = _der(data, 0)
    fields, at = [], 0
    while at < len(body):
        field_tag, value, at = _der(body, at)
        fields.append((field_tag, value))
    if tag != 0x30 or end != len(data) or [t for t, _ in fields[:4]] != [0x16, 0x16, 0x16, 4] \
            or fields[0][1] != b"IM4P" or (kind is not None and fields[1][1] != kind):
        raise Failed("unexpected Apple image")
    return fields[3][1]


def lzfse_decode(data, limit=256 << 20):
    lib = ctypes.CDLL(ctypes.util.find_library("lzfse") or "liblzfse.so.1")
    lib.lzfse_decode_buffer.restype = ctypes.c_size_t
    lib.lzfse_decode_buffer.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p,
                                        ctypes.c_size_t, ctypes.c_void_p]
    out = ctypes.create_string_buffer(limit)
    size = lib.lzfse_decode_buffer(out, limit, data, len(data), None)
    if not 0 < size < limit:
        raise Failed("cannot decode Apple's kernel collection")
    return out.raw[:size]


import ctypes.util  # noqa: E402  (find_library lives in a submodule)


def apple_kernel_collection(im4p_path, output):
    packed = im4p_payload(Path(im4p_path).read_bytes(), b"krnl")
    data = lzfse_decode(packed)
    if data[:4] == bytes.fromhex("cafebabe"):
        count, = struct.unpack_from(">I", data, 4)
        arm = [struct.unpack_from(">5I", data, 8 + 20 * i) for i in range(count)]
        arm = [s for s in arm if s[0] == 0x100000C]
        if len(arm) != 1:
            raise Failed("unexpected kernel collection")
        data = data[arm[0][2]:arm[0][2] + arm[0][3]]
    if hashlib.sha256(data).hexdigest() != macos_inputs.APPLE_KC_SHA256:
        raise Failed("Apple's kernel collection is not the pinned 27.2 one")
    Path(output).write_bytes(data)


def trust_module(path):
    # trst, trcs, trca: static, SystemOS and AppOS caches; the pinned bundle
    # digest checks all three together.
    module = im4p_payload(Path(path).read_bytes(), None)
    if struct.unpack_from("<I", module)[0] != 2 or \
            len(module) != 24 + struct.unpack_from("<I", module, 20)[0] * 24:
        raise Failed("not a complete version-2 trust cache")
    return module


def trust_bundle(modules):
    offsets, cursor = [], 4 + 4 * len(modules)
    for module in modules:
        offsets.append(cursor)
        cursor += len(module)
    return struct.pack(f"<{len(modules) + 1}I", len(modules), *offsets) + b"".join(modules)


def cdhash(path):
    """CDHash of a thin signed Mach-O: SHA-256 of its CodeDirectory, cut to 20 bytes."""
    data = Path(path).read_bytes()
    if data[:4] != b"\xcf\xfa\xed\xfe":
        return None
    count, = struct.unpack_from("<I", data, 16)
    at = 32
    for _ in range(count):
        command, size = struct.unpack_from("<II", data, at)
        if command == 0x1D:
            offset, length = struct.unpack_from("<II", data, at + 8)
            blob = data[offset:offset + length]
            magic, _, blobs = struct.unpack_from(">III", blob, 0)
            if magic != 0xFADE0CC0:
                raise Failed(f"{path}: no embedded signature")
            for index in range(blobs):
                _, start = struct.unpack_from(">II", blob, 12 + 8 * index)
                cd_magic, cd_length = struct.unpack_from(">II", blob, start)
                if cd_magic == 0xFADE0C02 and blob[start + 37] == 2:
                    return hashlib.sha256(blob[start:start + cd_length]).digest()[:20]
            raise Failed(f"{path}: no SHA-256 CodeDirectory")
        at += size
    raise Failed(f"{path}: not signed")


def boot_trust(kit, stock):
    """The kit's code (the Metal plugin) in one module before Apple's three."""
    count, = struct.unpack_from("<I", stock)
    starts = struct.unpack_from(f"<{count}I", stock, 4)
    originals = [stock[s:e] for s, e in zip(starts, list(starts[1:]) + [len(stock)])]
    plugin = kit.path("GPUBundles")
    hashes = sorted({h for f in sorted(plugin.rglob("*")) if f.is_file() and (h := cdhash(f))})
    ident = uuid.uuid5(uuid.NAMESPACE_URL, "droidvm-macos-gpu:" + "".join(h.hex() for h in hashes))
    own = struct.pack("<I16sI", 2, ident.bytes, len(hashes)) + b"".join(h + bytes([2, 0, 0, 0]) for h in hashes)
    return trust_bundle([own, *originals])


def link_kernel_collection(kit, apple_kc, output, log, verify=True):
    """Relink: Apple's kexts stay where they are, the kit's kernel and kexts join them."""
    from .macos_vendor import kc_build
    spec = kit.manifest["kc"]
    kc_build.DATA_CONST_START = spec["data_const_start"]
    kc_build.DATA_START = spec["data_start"]
    kc_build.KEPT_POINTER_REBINDS = [(int(a, 16), s) for a, s in spec["rebinds"]]
    with tempfile.TemporaryDirectory(prefix=".kexts-", dir=Path(output).parent) as tmp:
        natives = []
        for name in spec["native"]:
            descriptor = json.loads(kit.path(f"kexts/{name}.json").read_text())
            objects = Path(tmp) / f"{name}.objects.json"
            objects.write_text(json.dumps([str(kit.path(p)) for p in descriptor["objects"]]))
            descriptor["objects"] = str(objects)
            path = Path(tmp) / f"{name}.json"
            path.write_text(json.dumps(descriptor))
            natives.append(str(path))
        args = argparse.Namespace(kc=str(apple_kc), kernel=str(kit.path("kernel.macho")),
                                  output=str(output), keep=spec["keep"], native=natives, check=False)
        with open(log, "a") as stream, contextlib.redirect_stdout(stream):
            try:
                kc_build.build(args)
            except SystemExit as exc:
                raise Failed("cannot link the macOS kernel collection: " + str(exc)) from exc
    if verify and macos_inputs.digest(output) != spec["sha256"]:
        raise Failed("The linked kernel collection differs from the qualified build")


# --------------------------------------------------------------- disk ------

def seed(logical):
    from .macos_vendor import packed_ramdisk
    data = struct.pack("<8sIIQQ", b"DBRDISK1", 1, PAGE, logical, 0)
    packed_ramdisk.regions(data)
    return data, packed_ramdisk.logical_hash(logical, [])


def create_store(path, logical):
    from .macos_vendor import grow_data_store as grow
    pages = logical // PAGE
    bitmap_blocks = (pages + 8 * PAGE - 1) // (8 * PAGE)
    offset = (2 + bitmap_blocks) * PAGE
    _, identity = seed(logical)
    with open(path, "xb") as handle:
        handle.truncate(offset + logical)
        handle.write(LABEL + bytes(PAGE - len(LABEL)))
        handle.write(grow.header(pages, identity, 1))
    return offset


def build_container(store, offset, logical, data_uuid):
    from .macos_vendor import apfs_add_volumes as volumes
    image = volumes.grow.Image(store, offset, logical)
    roles = volumes.role_volumes(image)
    if list(roles) != [volumes.ROLES["S"]]:
        raise Failed("unexpected macOS system image")
    if str(roles[volumes.ROLES["S"]][1]).upper() != macos_inputs.SYSTEM_VOLUME_UUID:
        raise Failed("unexpected macOS system volume")
    specs = [("Data", volumes.ROLES["D"], False, data_uuid)] + \
            [(n, volumes.ROLES[r], False, str(uuid.uuid4()).upper()) for n, r in OTHER_VOLUMES]
    size = macos_inputs.MEMBERS["os.aea"]["plain_size"]
    with contextlib.redirect_stdout(io.StringIO()):
        volumes.add_volumes(image, specs, logical if logical > size else None,
                            roles[volumes.ROLES["S"]][0])
    image.map.close()
    image.handle.close()
    image = volumes.grow.Image(store, offset, logical)
    roles = volumes.role_volumes(image)
    image.map.close()
    image.handle.close()
    names = {"S": "system", "D": "data", **{r: n.lower() for n, r in OTHER_VOLUMES}}
    return {names[volumes.ROLE_NAMES[r]]: {"index": i, "uuid": str(u).upper()}
            for r, (i, u) in roles.items()}


def mark_data(store, logical):
    from .macos_vendor.data_store import Store
    disk = Store(store, writable=True)
    try:
        disk.check(logical, [])
        return disk.mark_data()
    finally:
        disk.close()


# ------------------------------------------------------- helper VM ---------

def fat_image(path, size_mib, files):
    """A FAT exchange disk written with mtools: FILES {name: bytes}, no root needed."""
    env = {**os.environ, "MTOOLS_SKIP_CHECK": "1", "TZ": "UTC"}
    with open(path, "wb") as handle:
        handle.truncate(size_mib << 20)
    subprocess.run(["mformat", "-F", "-v", "GVMX", "-i", str(path), "::"], check=True, env=env,
                   capture_output=True)
    with tempfile.TemporaryDirectory(prefix=".fat-", dir=Path(path).parent) as tmp:
        for name, data in files.items():
            source = Path(tmp) / name
            source.parent.mkdir(parents=True, exist_ok=True)
            source.write_bytes(data)
        for entry in sorted(Path(tmp).iterdir()):
            subprocess.run(["mcopy", "-s", "-Q", "-m", "-i", str(path), str(entry), "::/"],
                           check=True, env=env, capture_output=True)


def fat_read(path, name):
    env = {**os.environ, "MTOOLS_SKIP_CHECK": "1"}
    result = subprocess.run(["mtype", "-i", str(path), "::/" + name], capture_output=True, env=env)
    return result.stdout if result.returncode == 0 else None


def q(value):
    return shlex.quote(str(value))


def fill_job(disk, offset, logical):
    """Helper boot 1: System <- SystemOS cryptex, Preboot <- AppOS cryptex, Data <- template.

    One volume of the container is mounted read-write at a time and its source
    comes from another device: with several volumes mounted together the APFS
    driver has crashed in large copies (upstream make-macos-disk.py). Copies
    are tar pipes, which like upstream's rsync -aH keep hard links and numeric
    owners and replace a symlink in the system with the cryptex's directory.
    """
    system, preboot, data = disk["system"]["index"], disk["preboot"]["index"], disk["data"]["index"]
    excludes = " ".join("--exclude=./" + e for e in CRYPTEX_EXCLUDES)
    return f"""
copy() {{ (set -f; cd "$1" && tar -cf - --numeric-owner $3 .) | (cd "$2" && tar -xpf - --numeric-owner); }}
store=$(disk gvm-store); systemos=$(disk gvm-systemos); appos=$(disk gvm-appos); scratch=$(disk gvm-scratch)
c=$(losetup --find --show --offset {offset} --sizelimit {logical} "$store")
mkdir -p /m/v /m/s /m/t
echo step template
mount -t ext4 "$scratch" /m/t
mount -t apfs -o ro,vol={system} "$c" /m/v
mkdir -p /m/t/template
copy /m/v/{TEMPLATE} /m/t/template
mkdir -p /x/export
cp /m/v/{TEMPLATE}/{DSLOCAL}/groups/admin.plist /x/export/admin.plist
cp /m/v/{TEMPLATE}/{DSLOCAL}/groups/staff.plist /x/export/staff.plist
umount /m/v
echo step system
s=$(losetup --find --show --read-only "$systemos")
mount -t apfs -o ro "$s" /m/s
mount -t apfs -o readwrite,vol={system} "$c" /m/v
copy /m/s /m/v "{excludes}"
mkdir -p /m/v/System/Volumes/Preboot/Cryptexes /m/v/System/Volumes/Preboot/com.apple.security.cryptexd/Cryptexes
ln -sfn / /m/v/System/Volumes/Preboot/Cryptexes/OS
umount /m/v
umount /m/s
losetup -d "$s"
echo step preboot
s=$(losetup --find --show --read-only "$appos")
mount -t apfs -o ro "$s" /m/s
mount -t apfs -o readwrite,vol={preboot} "$c" /m/v
mkdir -p /m/v/Cryptexes/App /m/v/com.apple.security.cryptexd/Cryptexes
ln -sfn / /m/v/Cryptexes/OS
copy /m/s /m/v/Cryptexes/App
umount /m/v
umount /m/s
losetup -d "$s"
echo step data
mount -t apfs -o readwrite,vol={data} "$c" /m/v
copy /m/t/template /m/v
for d in tmp log run vm folders db/dslocal/nodes/Default; do mkdir -p /m/v/private/var/$d; done
mkdir -p /m/v/private/tmp
chmod 1777 /m/v/private/var/tmp /m/v/private/tmp
umount /m/v
umount /m/t
losetup -d "$c"
echo step done
"""


def stage_job(disk, offset, logical, operations):
    data = disk["data"]["index"]
    lines = ["store=$(disk gvm-store)",
             f"c=$(losetup --find --show --offset {offset} --sizelimit {logical} \"$store\")",
             "mkdir -p /m/v", f"mount -t apfs -o readwrite,vol={data} \"$c\" /m/v", "cd /m/v"]
    lines += operations
    lines += ["cd /", "umount /m/v", "losetup -d \"$c\"", "echo step done"]
    return "\n".join(lines) + "\n"


class Staging:
    """Data-volume changes as files on the exchange disk plus the commands that place them."""

    def __init__(self):
        self.files, self.ops = {}, []

    def put(self, dest, data, mode=0o644, owner="0:0"):
        name = f"f/{len(self.files):04d}"
        self.files[name] = data
        dest = dest.lstrip("/")
        self.ops += [f"mkdir -p {q(os.path.dirname(dest) or '.')}",
                     f"rm -rf {q(dest)}", f"cp /x/{name} {q(dest)}",
                     f"chown {owner} {q(dest)}", f"chmod {mode:o} {q(dest)}"]

    def tree(self, source, dest):
        source = Path(source)
        for path in sorted(source.rglob("*")):
            relative = path.relative_to(source)
            if path.is_symlink():
                self.ops.append(f"ln -sfn {q(os.readlink(path))} {q(Path(dest) / relative)}")
            elif path.is_dir():
                self.ops.append(f"mkdir -p {q(Path(dest) / relative)}")
            else:
                data = path.read_bytes()
                # Mach-O code is executable in the guest whatever the package mode.
                self.put(str(Path(dest) / relative), data,
                         0o755 if data[:4] in (b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe") else 0o644)
        self.ops.append(f"chown -R 0:0 {q(dest)}")

    def run(self, command):
        self.ops.append(command)


def user_record(name, password, uid=501):
    """A local administrator as System Settings writes one (upstream stage-gpu-plugin.py).

    Only the salted PBKDF2 hash is kept, so a paused creation never stores
    the password itself."""
    salt, iterations = os.urandom(32), 50000
    entropy = hashlib.pbkdf2_hmac("sha512", password.encode(), salt, iterations, 128)
    shadow = plistlib.dumps({"SALTED-SHA512-PBKDF2": {"entropy": entropy, "iterations": iterations,
                                                       "salt": salt}}, fmt=plistlib.FMT_BINARY)
    guid = str(uuid.uuid4()).upper()
    user = plistlib.dumps({
        "name": [name], "realname": [name], "uid": [str(uid)], "gid": ["20"],
        "home": ["/Users/" + name], "shell": ["/bin/zsh"], "generateduid": [guid],
        "passwd": ["********"], "authentication_authority": [";ShadowHash;HASHLIST:<SALTED-SHA512-PBKDF2>"],
        "ShadowHashData": [shadow], "_writers_passwd": [name], "_writers_realname": [name],
        "_writers_hint": [name]}, fmt=plistlib.FMT_BINARY)
    return user, guid


def group_records(name, guid, admin, staff):
    groups = {}
    for group, raw in (("admin", admin), ("staff", staff)):
        record = plistlib.loads(raw)
        record["users"] = [u for u in record.get("users", []) if u != name] + [name]
        record["groupmembers"] = [g for g in record.get("groupmembers", []) if g != guid] + [guid]
        groups[group] = plistlib.dumps(record, fmt=plistlib.FMT_BINARY)
    return groups


def launch_daemon(label, command):
    return plistlib.dumps({"Label": label, "RunAtLoad": True,
                           "ProgramArguments": ["/bin/sh", "-c", command]}, fmt=plistlib.FMT_XML)


def staging(kit, account, user, guid, public_key, admin, staff):
    s = Staging()
    s.run("mkdir -p Library/GPUBundles")
    s.run("rm -rf Library/GPUBundles/NativeAdrenoMetal.bundle")
    s.tree(kit.path("GPUBundles/NativeAdrenoMetal.bundle"), "Library/GPUBundles/NativeAdrenoMetal.bundle")
    s.run("chmod -R go-w,a+rX Library/GPUBundles/NativeAdrenoMetal.bundle")
    s.put("Library/LaunchDaemons/org.droidvm.run.0.plist", launch_daemon("org.droidvm.run.0", "sleep 0; " + SSHD))
    s.put("./private/var/db/com.apple.xpc.launchd/disabled.plist",
          plistlib.dumps({label: True for label in DISABLED_JOBS}, fmt=plistlib.FMT_XML))
    s.run("rm -f " + q("./private/var/containers/Shared/SystemGroup/systemgroup.com.apple.mobilegestaltcache/"
                       "Library/Caches/com.apple.MobileGestalt.plist"))
    # As upstream's staging: no debug logging configuration in a new seed.
    s.run("rm -rf " + q("Library/Preferences/Logging/Subsystems"))
    s.put(f"{DSLOCAL}/users/{account}.plist", user, 0o600)
    for group, data in group_records(account, guid, admin, staff).items():
        s.put(f"{DSLOCAL}/groups/{group}.plist", data, 0o600)
    home = "/Users/" + account
    s.put("Library/LaunchDaemons/org.droidvm.user-home.plist", launch_daemon(
        "org.droidvm.user-home",
        f"until id {account}; do sleep 5; done; [ -d {home} ] && exit 0; createhomedir -c -u {account} "
        f"2>/dev/null; touch {home}/.skipbuddy; chown -R {account}:staff {home}; ls -la {home}"))
    s.put("./private/var/db/.AppleSetupDone", b"", 0o444)
    s.put("Library/Managed Preferences/com.apple.SetupAssistant.managed.plist",
          plistlib.dumps({"SkipSetupItems": SETUP_SKIP_ITEMS}, fmt=plistlib.FMT_XML))
    s.put("Library/Preferences/com.apple.keyboardtype.plist",
          plistlib.dumps({"keyboardtype": {"4178-6900-0": 40}}, fmt=plistlib.FMT_XML))
    s.run("[ -L System/Cryptexes ] || ln -s /System/Cryptexes System/Cryptexes")
    s.put("./private/var/root/.ssh/authorized_keys", public_key.encode() + b"\n", 0o600)
    s.run("chmod 700 ./private/var/root/.ssh")
    return s


# ---------------------------------------------------------------- boot -----

def fdt_reserved_top(fdt):
    """Bytes of crosvm's simplefb framebuffer and droidvm,pool regions at the top of RAM."""
    from .macos_vendor import make_droidvm_adt as adt
    nodes = adt.parse_fdt(Path(fdt).read_bytes())
    total = 0
    for props in nodes.values():
        compatible = props.get("compatible", b"")
        if b"droidvm,pool" in compatible or compatible.startswith(b"simple-framebuffer\0"):
            total += sum(size for _, size in adt.reg_pairs(props["reg"]))
    return total


def make_adt(kit, output):
    spec = kit.manifest["boot"]
    fdt = kit.path("board.dtb")
    dram = PROFILE["memory_mib"] * 1024 * 1024 - 0x600000 - fdt_reserved_top(fdt)
    tool = Path(__file__).resolve().parent / "macos_vendor/make_droidvm_adt.py"
    subprocess.run([sys.executable, "-I", str(tool), "--fdt", str(fdt), "--dram-size", hex(dram),
                    *spec["adt_args"], "--root-match-volume-uuid", macos_inputs.SYSTEM_VOLUME_UUID,
                    "--output", str(output)], check=True, capture_output=True)


def boot_disk(kit, kc, trust, adt, data_seed, output):
    spec = kit.manifest["boot"]
    env = {**os.environ, "MTOOLS_SKIP_CHECK": "1", "TZ": "UTC"}
    with tempfile.TemporaryDirectory(prefix=".boot-", dir=Path(output).parent) as tmp:
        stage = Path(tmp)
        payloads = stage / "EFI/UniXNU/Payloads"
        payloads.mkdir(parents=True)
        (stage / "EFI/BOOT").mkdir(parents=True)
        shutil.copyfile(kit.path("efi/BOOTAA64.EFI"), stage / "EFI/BOOT/BOOTAA64.EFI")
        shutil.copyfile(kit.path("efi/DarwinBoot.efi"), payloads / "DarwinBoot.efi")
        shutil.copyfile(kc, payloads / "kernel.macho")
        shutil.copyfile(trust, payloads / "kernel.macho.trustcache")
        (payloads / "scratch-data.img").write_bytes(data_seed)
        shutil.copyfile(adt, payloads / "kernel.macho.adt")
        (stage / "EFI/UniXNU/unixnu.toml").write_text(
            'version = 1\n[boot]\ntimeout = 3\ndefault = "xnu"\n[kits]\nautoload = false\n'
            '[[driver]]\npath = "Payloads/DarwinBoot.efi"\n[[entry]]\nid = "xnu"\n'
            'title = "macOS 27.2"\ntype = "xnu"\npath = "Payloads/kernel.macho"\n'
            'initrd = "Payloads/scratch-data.img"\n' f'args = "{spec["bootargs"]}"\n')
        partial = Path(str(output) + ".part")
        partial.unlink(missing_ok=True)
        subprocess.run(["mformat", "-C", "-F", "-T", str(spec["boot_mib"] * 2048), "-v", "MACOSESP",
                        "-i", str(partial), "::"], check=True, env=env, capture_output=True)
        for entry in sorted(stage.iterdir()):
            subprocess.run(["mcopy", "-i", str(partial), "-s", "-Q", "-m", str(entry), "::/"],
                           check=True, env=env, capture_output=True)
        os.replace(partial, output)


# ----------------------------------------------------------- creation ------

def space_needed(disk_gib):
    """Peak bytes of a creation: the filled disk (about 20 GiB) beside the
    decrypted system cryptex and the helper's template scratch, or the system
    archive beside its decrypted image, plus headroom."""
    return 27 << 30


class Creation:
    """One resumable creation. progress(phase, fraction 0..1, detail); cancel() -> bool."""

    def __init__(self, store, name, account, password=None, disk_gib=48, progress=None,
                 cancel=None, kit=None, helper=None, keep_downloads=False):
        from .core import vm_name
        self.store = Path(store).expanduser().resolve()
        self.name = vm_name(name)
        self.account = valid_account(account)
        self.password = password
        if not DISK_LIMITS[0] <= disk_gib <= DISK_LIMITS[1]:
            raise Failed(f"macOS disk size must be {DISK_LIMITS[0]}..{DISK_LIMITS[1]} GiB")
        self.disk_gib = disk_gib
        self.logical = disk_gib << 30
        self.progress = progress or (lambda phase, fraction, detail="": None)
        self.cancel = cancel or (lambda: False)
        self.kit = kit or Kit()
        self.helper = helper or run_helper_vm
        self.keep_downloads = keep_downloads
        self.downloads = self.store / ".macos-downloads"
        self.work = self.store / ".creating" / self.name
        self.state_path = self.work / "state.json"
        self.state = {}

    # -- bookkeeping --
    def _load(self):
        if self.state_path.is_file():
            self.state = json.loads(self.state_path.read_text())
            if self.state.get("account") != self.account or self.state.get("disk_gib") != self.disk_gib:
                raise Failed("An unfinished creation of this VM used other settings; "
                             "discard it first")
        else:
            self.state = {"schema": 1, "name": self.name, "account": self.account,
                          "disk_gib": self.disk_gib, "keep_downloads": self.keep_downloads,
                          "kit": self.kit.manifest["version"], "done": {}}

    def _save(self):
        storage.atomic_json(self.state_path, self.state)

    def done(self, step):
        return step in self.state["done"]

    def mark(self, step, **result):
        self.state["done"][step] = {"time": time.time(), **result}
        self._save()

    def check(self):
        if self.cancel():
            raise Cancelled()

    def log(self):
        return self.work / "create.log"

    # -- steps --
    def preflight(self):
        if os.geteuid() == 0:
            raise Failed("Create VMs as the ordinary owner, not root")
        if not self.store.is_relative_to(Path.home().resolve()):
            raise Failed("VM storage must be inside the owner home directory")
        vm = self.store / "vms" / self.name
        if vm.exists() or vm.is_symlink():
            raise Failed("A VM with this name already exists")
        self.kit.verify()
        for path in (self.store, self.downloads, self.work.parent, self.work):
            path.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._load()
        if not self.done("publish"):
            need = space_needed(self.disk_gib) - self._existing_bytes()
            free = shutil.disk_usage(self.work).free
            if free < need:
                raise Failed(f"Creating macOS needs about {need / 2**30:.0f} GiB of free space "
                             f"in {self.store}; {free / 2**30:.0f} GiB are free")

    def _existing_bytes(self):
        total = 0
        for root in (self.work, self.downloads):
            for path in root.rglob("*"):
                if path.is_file() and not path.is_symlink():
                    total += path.stat().st_blocks * 512
        return total

    def fetch(self, name, weight_from, weight_to):
        size = macos_inputs.MEMBERS[name]["compressed"]

        def report(done, total):
            self.progress("download", weight_from + (weight_to - weight_from) * done / total,
                          f"{macos_inputs.MEMBERS[name]['member']} {done >> 20}/{total >> 20} MiB")
        return macos_inputs.fetch(name, self.downloads, report, self.cancel)

    def run(self):
        self.preflight()
        with storage.operation_lock(self.work):
            try:
                return self._run()
            except Cancelled:
                self.progress("cancelled", 0, "Creation paused; run it again to resume")
                raise

    def _run(self):
        w, kit = self.work, self.kit
        vm = w / "vm"
        vm.mkdir(mode=0o700, exist_ok=True)
        if not self.done("account"):
            if not self.password:
                raise Failed("A password for the macOS account is required")
            user, guid = user_record(self.account, self.password)
            path = w / "user.plist"
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as out:
                out.write(user)
            self.mark("account", guid=guid)
        self.password = None
        # Small inputs first: a broken network fails before the large downloads.
        if not self.done("inputs"):
            for index, name in enumerate(("kernelcache", "os-trustcache", "system-trustcache",
                                          "app-trustcache", "app-cryptex")):
                self.check()
                self.fetch(name, 0.0 + index * 0.002, 0.002 + index * 0.002)
            self.mark("inputs")
        if not self.done("kc"):
            self.progress("link", 0.012, "Linking the macOS kernel collection")
            apple = w / "apple-kc.bin"
            apple_kernel_collection(self.downloads / "kernelcache", apple)
            link_kernel_collection(kit, apple, w / "kc.bin", self.log())
            apple.unlink()
            self.mark("kc", sha256=macos_inputs.digest(w / "kc.bin"))
        if not self.done("trust"):
            stock = trust_bundle([trust_module(self.downloads / n) for n in
                                  ("os-trustcache", "system-trustcache", "app-trustcache")])
            if hashlib.sha256(stock).hexdigest() != macos_inputs.STOCK_TRUST_SHA256:
                raise Failed("Apple's trust caches are not the pinned 27.2 ones")
            trust = boot_trust(kit, stock)
            (w / "trust.bin").write_bytes(trust)
            self.mark("trust", sha256=hashlib.sha256(trust).hexdigest())
        store = vm / "disk.raw"
        if not self.done("store"):
            store.unlink(missing_ok=True)
            offset = create_store(store, self.logical)
            self.mark("store", offset=offset)
        offset = self.state["done"]["store"]["offset"]
        if not self.done("os"):
            self.fetch("os.aea", 0.015, 0.75)
            self.check()
            self.progress("decrypt", 0.75, "Decrypting the macOS system image")
            from . import macos_aea
            spec = macos_inputs.MEMBERS["os.aea"]
            size, sha = macos_aea.decrypt(
                self.downloads / "os.aea", store, offset,
                progress=lambda d, t: self.progress("decrypt", 0.75 + 0.05 * d / t, ""),
                cancel=self.cancel)
            if size != spec["plain_size"] or sha != spec["plain_sha256"]:
                raise Failed("The decrypted macOS system image is not the pinned 27.2 one")
            if not self.keep_downloads:
                (self.downloads / "os.aea").unlink()
            self.mark("os")
        if not self.done("container"):
            self.progress("disk", 0.8, "Creating the APFS volumes")
            from .macos_vendor import apfs_add_volumes
            apfs_add_volumes.MKAPFS = kit.path("bin/mkapfs")
            disk = build_container(store, offset, self.logical, str(uuid.uuid4()).upper())
            self.mark("container", disk=disk)
        disk = self.state["done"]["container"]["disk"]
        systemos = w / "systemos.raw"
        if not self.done("systemos"):
            self.fetch("system-cryptex.aea", 0.8, 0.85)
            self.check()
            self.progress("decrypt", 0.85, "Decrypting the system cryptex")
            from . import macos_aea
            spec = macos_inputs.MEMBERS["system-cryptex.aea"]
            systemos.unlink(missing_ok=True)
            size, sha = macos_aea.decrypt(self.downloads / "system-cryptex.aea", systemos, 0,
                                          cancel=self.cancel)
            if size != spec["plain_size"] or sha != spec["plain_sha256"]:
                raise Failed("The decrypted system cryptex is not the pinned 27.2 one")
            if not self.keep_downloads:
                (self.downloads / "system-cryptex.aea").unlink()
            self.mark("systemos")
        if not self.done("fill"):
            self.check()
            self.progress("disk", 0.86, "Writing the macOS volumes (helper VM)")
            exchange, scratch = w / "exchange.img", w / "scratch.img"
            fat_image(exchange, 16, {"job.sh": fill_job(disk, offset, self.logical).encode()})
            scratch.unlink(missing_ok=True)
            with open(scratch, "wb") as handle:
                handle.truncate(4 << 30)
            subprocess.run(["mke2fs", "-q", "-F", "-t", "ext4", "-E", "root_owner=0:0",
                            "-O", "^has_journal", str(scratch)], check=True)
            self.helper(w, [("gvm-store", store, False), ("gvm-systemos", systemos, True),
                            ("gvm-appos", self.downloads / "app-cryptex", True),
                            ("gvm-exchange", exchange, False), ("gvm-scratch", scratch, False)],
                        "fill", self)
            admin, staff = fat_read(exchange, "export/admin.plist"), fat_read(exchange, "export/staff.plist")
            if not admin or not staff:
                raise Failed("The helper VM did not export the account templates")
            (w / "admin.plist").write_bytes(admin)
            (w / "staff.plist").write_bytes(staff)
            scratch.unlink()
            exchange.unlink()
            systemos.unlink(missing_ok=True)
            self.mark("fill")
        key = vm / "agent-key"
        if not self.done("stage"):
            self.check()
            self.progress("stage", 0.95, "Adding your account and the graphics driver")
            from . import macos
            for leftover in (key, Path(str(key) + ".pub")):
                leftover.unlink(missing_ok=True)
            identity = w / "identity"
            shutil.rmtree(identity, ignore_errors=True)
            macos.create_control_identity(identity)
            os.replace(identity / "agent-key", key)
            public = (identity / "agent-key.pub").read_text().strip()
            shutil.rmtree(identity)
            s = staging(kit, self.account, (w / "user.plist").read_bytes(),
                        self.state["done"]["account"]["guid"], public,
                        (w / "admin.plist").read_bytes(), (w / "staff.plist").read_bytes())
            exchange = w / "exchange.img"
            fat_image(exchange, 128, {"job.sh": stage_job(disk, offset, self.logical, s.ops).encode(),
                                      **s.files})
            self.helper(w, [("gvm-store", store, False), ("gvm-exchange", exchange, False)], "stage", self)
            exchange.unlink()
            present = mark_data(store, self.logical)
            self.mark("stage", pages=present, public_key=public)
        if not self.done("boot"):
            self.progress("boot", 0.98, "Writing the boot disk")
            adt = w / "kernel.macho.adt"
            make_adt(kit, adt)
            boot_disk(kit, w / "kc.bin", w / "trust.bin", adt, seed(self.logical)[0], vm / "boot.img")
            self.mark("boot", sha256=macos_inputs.digest(vm / "boot.img"))
        return self.publish(vm)

    def publish(self, prepared):
        from . import config
        target = self.store / "vms" / self.name
        if target.exists() or target.is_symlink():
            raise Failed("A VM with this name already exists")
        ident = str(uuid.uuid4())
        cfg = config.complete_new({
            "id": ident, "uid": os.getuid(), "gid": os.getgid(), "name": self.name,
            "os": "macos", "username": "root", "cpus": PROFILE["cpus"],
            "memory_mib": PROFILE["memory_mib"], "disk_gib": self.disk_gib,
            "memory_mode": "shared", "boot_managed": False, "guest_payload": False,
            "vhost_net": False, "install_media": False, "macos": dict(PROFILE),
            "subnet": f"10.203.{int(ident[:8], 16) % 250 + 1}.0/24",
            "source_image": "macOS 27.2 (26B8125) from Apple", "ssh_identity": str(target / "agent-key"),
        })
        cfg["display"].update(gpu="simplefb", fullscreen=True, width=1280, height=800, vnc=False)
        config.validate(cfg)
        manifest = {"schema": 4, "created": "apple-ipsw", "kit": self.kit.manifest["version"],
                    "disk_gib": self.disk_gib, "boot": dict(PROFILE), "account": self.account,
                    "inputs": {k: v["sha256"] for k, v in macos_inputs.MEMBERS.items()},
                    "files": {"boot.img": self.state["done"]["boot"]["sha256"],
                              "agent-key": macos_inputs.digest(prepared / "agent-key")}}
        storage.atomic_json(prepared / "inputs.json", manifest)
        storage.atomic_json(prepared / "config.json", cfg)
        storage.atomic_json(prepared / "state.json", {"state": "created", "clean_shutdown": None})
        for path in prepared.iterdir():
            path.chmod(0o600)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with storage.operation_lock(self.store):
            if target.exists():
                raise Failed("A VM with this name already exists")
            os.rename(prepared, target)
        self.progress("done", 1.0, "macOS is ready")
        self.cleanup()
        return target

    def cleanup(self):
        shutil.rmtree(self.work, ignore_errors=True)
        try:
            self.work.parent.rmdir()
        except OSError:
            pass
        if not self.keep_downloads:
            shutil.rmtree(self.downloads, ignore_errors=True)


def discard(store, name):
    """Remove an unfinished creation; downloads stay for the next attempt."""
    from .core import vm_name
    work = Path(store).expanduser().resolve() / ".creating" / vm_name(name)
    if work.is_dir() and not work.is_symlink():
        shutil.rmtree(work)
        return True
    return False


def saved_request(store, name):
    """Settings of an unfinished creation, to resume it without asking again."""
    from .core import vm_name
    path = Path(store).expanduser().resolve() / ".creating" / vm_name(name) / "state.json"
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return {"account": state["account"], "disk_gib": state["disk_gib"],
            "keep_downloads": state.get("keep_downloads", False)}


def pending(store):
    root = Path(store).expanduser() / ".creating"
    return sorted(p.name for p in root.iterdir() if (p / "state.json").is_file()) if root.is_dir() else []


# ------------------------------------------------------- helper runner -----

HELPER_ACTION = "/usr/libexec/gunyahvm/gunyahvm-helper"


def run_helper_vm(work, disks, phase, creation):
    """Run the assembly helper VM through the fixed polkit action, as the owner."""
    spec = {"phase": phase, "disks": [[serial, str(path), ro] for serial, path, ro in disks]}
    storage.atomic_json(Path(work) / "helper.json", spec)
    log = Path(work) / f"helper-{phase}.log"
    with open(log, "ab") as stream:
        proc = subprocess.Popen(["pkexec", "--disable-internal-agent", HELPER_ACTION, "assemble",
                                 str(Path(work).resolve())], stdout=stream, stderr=stream,
                                start_new_session=True)
        def signal_group(number):
            # pkexec runs as root for a moment before it drops to the owner.
            deadline = time.monotonic() + 10
            while proc.poll() is None:
                try:
                    os.killpg(proc.pid, number)
                    return
                except PermissionError:
                    if time.monotonic() > deadline:
                        raise
                    time.sleep(0.2)
                except ProcessLookupError:
                    return
        try:
            while proc.poll() is None:
                if creation.cancel():
                    signal_group(15)
                    try:
                        proc.wait(60)
                    except subprocess.TimeoutExpired:
                        signal_group(9)
                        proc.wait()
                    raise Cancelled()
                time.sleep(0.5)
        except BaseException:
            if proc.poll() is None:
                signal_group(9)
                proc.wait()
            raise
    check_helper(work, disks, phase, proc.returncode)


def check_helper(work, disks, phase, returncode):
    exchange = dict((serial, path) for serial, path, _ in disks)["gvm-exchange"]
    status = (fat_read(exchange, "status") or b"").strip()
    log = fat_read(exchange, "log.txt") or b""
    (Path(work) / f"helper-{phase}.job.log").write_bytes(log)
    if status != b"0":
        tail = log.decode(errors="replace").strip().splitlines()[-3:]
        raise Failed(f"The macOS assembly helper failed ({phase}, status "
                     f"{status.decode(errors='replace') or returncode}): " + " / ".join(tail))
