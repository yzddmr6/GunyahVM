# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Guard policy adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""Host facts and capability decisions, independent of board and distribution."""

from dataclasses import dataclass, field, asdict
import errno
import fcntl
import os
from pathlib import Path
import re
import struct

from . import components, devices, host

GUNYAH_CREATE_VM = 0x4700
GH_VM_CHECK_EXTENSION = 0x4117
GH_CAP_PROTECTED_SHARE = 1
WINDOWS_TEST_SWITCH = Path("/etc/gunyahvm/enable-windows-testing")


def windows_enabled():
    """System-wide maintainer opt-in, also visible after privileged launch."""
    return WINDOWS_TEST_SWITCH.is_file()


def protected_share(node):
    """Query an empty VM fd, never allocate/lend guest memory or start a VCPU."""
    fd = os.open(node, os.O_RDWR | os.O_CLOEXEC)
    try:
        vm = fcntl.ioctl(fd, GUNYAH_CREATE_VM, 0)
        try:
            try:
                return bool(
                    fcntl.ioctl(vm, GH_VM_CHECK_EXTENSION, GH_CAP_PROTECTED_SHARE)
                )
            except OSError as exc:
                if exc.errno in (errno.ENOTTY, errno.EINVAL):
                    return False
                raise
        finally:
            os.close(vm)
    finally:
        os.close(fd)


def msm_identity(node):
    """Read MSM GPU/chip IDs, without creating BOs, submitting work or powering a VM."""
    # include/uapi/drm/msm_drm.h: DRM_IOWR(0x40, drm_msm_param),
    # pipe=MSM_PIPE_3D0, scalar GET_PARAM; len/pad are zero.
    fd = os.open(node, os.O_RDONLY | os.O_CLOEXEC)
    try:
        values = []
        for parameter in (1, 3):  # MSM_PARAM_GPU_ID, MSM_PARAM_CHIP_ID
            buffer = bytearray(struct.pack("=IIQII", 0x10, parameter, 0, 0, 0))
            fcntl.ioctl(fd, 0xC0186440, buffer)
            values.append(struct.unpack_from("=Q", buffer, 8)[0])
        gpu, chip = values
        # Newer msm revisions may expose only the chip ID. Do not infer a
        # marketing name from the board or invent a model when GPU_ID is zero.
        return {
            "gpu_id": gpu,
            "chip_id": hex(chip),
            "model": f"Adreno {gpu}" if gpu else f"Adreno chip {chip:#x}",
        }
    finally:
        os.close(fd)


def text(path, default=""):
    try:
        return path.read_text().strip()
    except OSError:
        return default


def sensors(profile, root=Path("/")):
    batteries, temperatures = [], []
    for p in sorted((root / "sys/class/power_supply").glob("*")):
        if text(p / "type") != "Battery":
            continue
        names = profile["sensors"]["battery_names"]
        if names and p.name not in names:
            continue
        try:
            value = int(text(p / "capacity"))
            if 0 <= value <= 100:
                batteries.append(
                    {"name": p.name, "percent": value, "status": text(p / "status")}
                )
        except ValueError:
            pass
    for p in sorted((root / "sys/class/thermal").glob("thermal_zone*")):
        kind = text(p / "type")
        names = profile["sensors"]["thermal_types"]
        if names and kind not in names:
            continue
        try:
            value = int(text(p / "temp")) / 1000
            if -20 <= value <= 150:
                temperatures.append({"type": kind, "celsius": value})
        except ValueError:
            pass
    return {"batteries": batteries, "temperatures": temperatures}


def guard_reasons(profile, readings, running=False):
    guards = profile["guards"]
    why = []
    minimum = guards["stop_percent" if running else "min_start_percent"]
    maximum = guards["stop_c" if running else "max_start_c"]
    if any(b["percent"] < minimum for b in readings["batteries"]):
        why.append(f"Battery below {minimum}%")
    if any(t["celsius"] >= maximum for t in readings["temperatures"]):
        why.append(f"Temperature at or above {maximum} C")
    # Known tablet sources must be readable before launching. An unknown board
    # may be mains-powered; missing sensors remain UNKNOWN in diagnostics.
    for kind, key in [
        ("battery_names", "batteries"),
        ("thermal_types", "temperatures"),
    ]:
        if (profile["compatible"] or profile["sensors"][kind]) and not readings[key]:
            why.append(f"Configured {key} source unavailable")
    return why


@dataclass
class Snapshot:
    device_access: bool = False
    protected_share: bool | None = None
    firmware: dict = field(default_factory=dict)
    profile: dict = field(default_factory=lambda: devices.match([]))
    runtime: dict = field(default_factory=dict)
    render_nodes: list = field(default_factory=list)
    vhost_net: bool = False
    thp: str = ""
    memory: dict = field(default_factory=dict)
    cpus: list = field(default_factory=list)
    readings: dict = field(
        default_factory=lambda: {"batteries": [], "temperatures": []}
    )
    errors: dict = field(default_factory=dict)

    def as_dict(self):
        return {**asdict(self), "modes": self.modes(), "features": self.features()}

    def features(self):
        c = components.features(self.runtime, "crosvm")
        r = components.features(self.runtime, "renderer")
        g = components.features(self.runtime, "guest_kernel")
        m = components.features(self.runtime, "guest_mesa")
        gpu = any(n["accessible"] for n in self.render_nodes)
        share = (
            self.protected_share is True
            and "runtime-share" in c
            and "guest-accept" in g
        )
        virgl = gpu and "gpu" in c and "virgl" in r and "virtio-gpu" in g
        return {
            "headless": "gunyah" in c,
            "simplefb": "simplefb" in c,
            "2d": "gpu" in c and "virtio-gpu" in g,
            "virgl": virgl,
            "venus": virgl and share and "venus" in r,
            "native": virgl
            and share
            and "drm-msm" in r
            and "freedreno-virtio" in m
            and any(
                n["driver"] == "msm" and n["accessible"] for n in self.render_nodes
            ),
            "guest_accept": share,
            "shared": share and "shared-memory" in c and "shared-boot" in g,
            "vhost_net": self.vhost_net and "vhost-net" in c,
            "shares": "p9" in c,
            "vnc": "vnc" in c,
            "input_modes": "input-modes" in c,
            "balloon_disable": "balloon" in c,
            "windows": windows_enabled()
            and "windows-ecam" in components.features(self.runtime, "uefi")
            and "windows_drivers" in self.runtime.get("components", {}),
        }

    def modes(self, memory_mode="protected", guest_os="linux"):
        f = self.features()
        if guest_os == "windows11":
            return ["simplefb"] if f["simplefb"] and f["vnc"] and f["windows"] else []
        return ["none"] + [
            m
            for m in ("2d", "simplefb", "virgl", "venus", "native")
            if f[m] and not (memory_mode == "shared" and m == "native")
        ]

    def require_selection(self, cfg):
        reasons = []
        f = self.features()
        if not f["headless"]:
            reasons.append("Gunyah crosvm runtime is missing")
        if cfg["display"]["gpu"] not in self.modes(cfg["memory_mode"], cfg["os"]):
            reasons.append(
                "Selected display mode is unavailable with installed host/runtime/guest capabilities"
            )
        if cfg["memory_mode"] == "shared" and not f["shared"]:
            reasons.append(
                "Shared memory needs host runtime share and a matching guest payload"
            )
        if cfg["vhost_net"] and not f["vhost_net"]:
            reasons.append("vhost-net unavailable")
        if cfg["shares"] and (not f["shares"] or cfg["os"] == "windows11"):
            reasons.append("9p shared folders need a Linux guest and crosvm 9p support")
        if any(s["ro"] for s in cfg["shares"]):
            reasons.append("The installed 9p backend only supports writable exports")
        if (
            cfg["display"]["input_mode"] != "tablet" or cfg["display"]["keyboard_grab"]
        ) and not f["input_modes"]:
            reasons.append("Extended window input is unavailable in crosvm")
        if cfg["display"]["vnc"] and not f["vnc"]:
            reasons.append("VNC unavailable")
        if (
            cfg["display"]["gpu"] in ("2d", "virgl", "venus", "native")
            or cfg["memory_mode"] == "shared"
        ) and not cfg["guest_payload"]:
            reasons.append(
                "Selected display/memory mode requires the matching runtime guest payload"
            )
        if cfg["guest_payload"] and "guest_kernel" not in self.runtime.get(
            "components", {}
        ):
            reasons.append("Runtime guest kernel is not installed")
        if reasons:
            raise ValueError("; ".join(reasons))

    def require(self, cfg):
        self.require_selection(cfg)
        reasons = []
        if not self.device_access:
            reasons.append("/dev/gunyah is not accessible")
        if not self.firmware.get("hypervisor") or not self.firmware.get(
            "capability_ids"
        ):
            reasons.append("Gunyah RM firmware unavailable")
        if cfg["memory_mib"] > min(
            self.profile["resources"]["max_memory_mib"],
            host.memory_budget(self.memory.get("MemTotal", 0))["guest_max_mib"],
        ):
            reasons.append("Memory exceeds device/host budget")
        if cfg["cpus"] > min(self.profile["resources"]["max_cpus"], len(self.cpus)):
            reasons.append("vCPUs exceed online CPUs/device ceiling")
        needed = (
            cfg["memory_mib"]
            + (256 if cfg["os"] == "windows11" else 128)
            + host.memory_budget(self.memory.get("MemTotal", 0))["host_reserve_mib"]
        )
        if self.memory.get("MemAvailable", 0) < needed:
            reasons.append(f"Need {needed} MiB available host memory")
        if not re.search(r"\[(always|advise|within_size)\]", self.thp):
            reasons.append("shmem transparent hugepages unavailable")
        reasons += guard_reasons(self.profile, self.readings)
        if reasons:
            raise ValueError("; ".join(reasons))


def probe(root=Path("/"), manifest=None, profiles=None):
    snap = Snapshot()
    try:
        fw = host.firmware(
            root / "sys/firmware/devicetree/base", root / "sys/firmware/fdt"
        )
        snap.firmware = fw.summary()
    except (OSError, ValueError) as exc:
        snap.errors["firmware"] = str(exc)
    # Preserve board identity even when RM nodes/raw FDT are unavailable.
    compatible = snap.firmware.get("compatible") or host.strings(
        (text(root / "sys/firmware/devicetree/base/compatible")).encode()
    )
    snap.profile = devices.match(
        compatible, profiles or root / str(devices.DIRECTORY).lstrip("/")
    )
    dev = root / "dev/gunyah"
    snap.device_access = dev.is_char_device() and os.access(dev, os.R_OK | os.W_OK)
    if snap.device_access:
        try:
            snap.protected_share = protected_share(dev)
        except OSError as exc:
            snap.errors["protected_share"] = str(exc)
    try:
        snap.runtime = components.load(
            manifest or root / str(components.MANIFEST).lstrip("/"), root
        )
    except (OSError, ValueError, KeyError) as exc:
        snap.errors["runtime"] = str(exc)
    for p in sorted((root / "sys/class/drm").glob("renderD*")):
        node = root / "dev/dri" / p.name
        driver = (
            (p / "device/driver").resolve().name
            if (p / "device/driver").exists()
            else ""
        )
        # Exported GPU model varies with msm revisions. Do not infer it from board identity.
        model = text(p / "device/gpu_model") or text(p / "device/modalias")
        node_info = {
            "path": str(node),
            "driver": driver,
            "model": model,
            "accessible": node.is_char_device() and os.access(node, os.R_OK | os.W_OK),
        }
        if driver == "msm" and node_info["accessible"]:
            try:
                node_info.update(msm_identity(node))
            except OSError as exc:
                node_info["identity_error"] = str(exc)
        snap.render_nodes.append(node_info)
    snap.vhost_net = (root / "dev/vhost-net").is_char_device() and os.access(
        root / "dev/vhost-net", os.R_OK | os.W_OK
    )
    snap.thp = text(root / "sys/kernel/mm/transparent_hugepage/shmem_enabled")
    for line in text(root / "proc/meminfo").splitlines():
        key, value = line.split(":", 1)
        snap.memory[key] = int(value.split()[0]) // 1024
    allowed = os.sched_getaffinity(0)
    for p in sorted(
        (root / "sys/devices/system/cpu").glob("cpu[0-9]*"),
        key=lambda p: int(p.name[3:]),
    ):
        cpu = int(p.name[3:])
        if text(p / "online", "1") == "1" and (root != Path("/") or cpu in allowed):
            snap.cpus.append(
                {
                    "id": cpu,
                    "capacity": text(p / "cpu_capacity"),
                    "max_khz": text(p / "cpufreq/cpuinfo_max_freq"),
                    "cluster": text(p / "topology/cluster_id"),
                }
            )
    snap.readings = sensors(snap.profile, root)
    return snap


def diagnostic_rows(snap=None):
    snap = snap or probe()
    runtime_components = [
        name for name in snap.runtime.get("components", {})
        if snap.features()["windows"] or name not in ("uefi", "windows_drivers")
    ]
    rows = host.diagnostic_rows()
    rows += [
        (
            "profile",
            None if snap.profile["id"] == "unknown" else True,
            snap.profile["name"],
            "Unknown boards use conservative defaults.",
        ),
        (
            "runtime",
            bool(snap.runtime),
            snap.errors.get("runtime", ", ".join(runtime_components)),
            "Install matching arm64 runtime components.",
        ),
        (
            "protected-share",
            None if snap.protected_share is None else True,
            "unknown"
            if snap.protected_share is None
            else "available"
            if snap.protected_share
            else "unavailable; Venus/native/shared are disabled",
            "Blob/Venus need the runtime protected-share host ABI.",
        ),
        (
            "graphics",
            True,
            ", ".join(snap.modes()),
            "Only modes supported by host and installed guest payload are offered.",
        ),
        (
            "sensors",
            True
            if snap.readings["batteries"] and snap.readings["temperatures"]
            else None,
            str(snap.readings),
            "Inspect configured sysfs sources.",
        ),
    ]
    return rows
