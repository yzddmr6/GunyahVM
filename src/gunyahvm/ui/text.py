# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""User-facing names for backend values, sizes and known failure messages."""

from pathlib import Path
import re

from .i18n import N_, _, ngettext

BUSY = ("starting", "stopping", "restarting")
ACTIVE = ("running",) + BUSY

GRAPHICS = {
    "none": (N_("Off"), N_("No display. Use the console or SSH.")),
    "simplefb": (
        N_("Basic Display"),
        N_("A simple screen drawn by the host. Works with any guest kernel."),
    ),
    "2d": (N_("Standard 2D"), N_("A virtual GPU without 3D acceleration.")),
    "virgl": (
        N_("OpenGL Acceleration"),
        N_("OpenGL rendered by this device's GPU."),
    ),
    "venus": (
        N_("OpenGL and Vulkan Acceleration"),
        N_("OpenGL and Vulkan rendered by this device's GPU."),
    ),
    "native": (
        N_("Native Adreno GPU"),
        N_("The guest drives the Adreno GPU directly. Fastest, and the most demanding."),
    ),
}

INPUT = {
    "tablet": (N_("Pointer"), N_("Touch clicks where you tap.")),
    "touchpad": (N_("Touchpad"), N_("Drag to move the pointer and tap to click.")),
    "touch": (N_("Touchscreen"), N_("Multi-touch gestures go to the virtual machine.")),
}

NETWORK = {
    "nat": N_("Internet via This Device"),
    "off": N_("No Network"),
}

MIRRORS = {
    "official": N_("Ubuntu (Official)"),
    "tuna": N_("Tsinghua University Mirror"),
    "ustc": N_("USTC Mirror"),
}

RESOLUTIONS = [
    (1280, 720),
    (1280, 800),
    (1920, 1080),
    (1920, 1200),
    (2560, 1440),
    (2560, 1600),
]


def busy(action):
    return {
        "start": _("Starting…"),
        "stop": _("Shutting down…"),
        "restart": _("Restarting…"),
        "force": _("Forcing off…"),
        "delete": _("Deleting…"),
    }[action]


def graphics(mode):
    return _(GRAPHICS[mode][0])


def graphics_help(mode):
    return _(GRAPHICS[mode][1])


def number(value):
    """1, 1.5, 2.25 — no trailing zeros."""
    return f"{value:.2f}".rstrip("0").rstrip(".")


def gigabytes(mib):
    return _("{size} GB").format(size=number(mib / 1024))


def disk(gib):
    return _("{size} GB").format(size=gib)


def cores(count):
    return ngettext("{count} core", "{count} cores", count).format(count=count)


def resolution(width, height):
    return f"{width} × {height}"


def memory_choices(minimum, maximum, current=None):
    """Half-gigabyte steps between the limits, plus an unusual stored value."""
    values = list(range(512 * -(-minimum // 512), maximum + 1, 512))
    if minimum not in values and minimum <= maximum:
        values.insert(0, minimum)
    if current and current not in values:
        values = sorted(values + [current])
    return values or [minimum]


def system(cfg):
    if cfg.get("os") == "windows11":
        edition = cfg.get("windows_edition") or ""
        return _("Windows 11 {edition}").format(edition=edition).strip()
    source = cfg.get("source_image") or ""
    if source.startswith("Ubuntu "):
        return " ".join(source.split()[:2])
    if source:
        return _("Linux ({image})").format(image=Path(source).name)
    return _("Linux")


def state(status):
    """(label, kind) for a VM status; kind is running, busy, stopped, warning or error."""
    value = status.get("state")
    if value == "running":
        return _("Running"), "running"
    if value == "starting":
        return _("Starting…"), "busy"
    if value == "stopping":
        return _("Shutting down…"), "busy"
    if value == "restarting":
        return _("Restarting…"), "busy"
    if value == "invalid":
        return _("Unavailable"), "error"
    if value == "failed":
        if status.get("forced_stop"):
            return _("Forced off"), "stopped"
        if status.get("guard_reason"):
            return _("Stopped to protect the device"), "warning"
        return _("Stopped unexpectedly"), "error"
    if value == "interrupted":
        return _("Stopped unexpectedly"), "error"
    if status.get("guard_reason"):
        return _("Stopped to protect the device"), "warning"
    return _("Stopped"), "stopped"


CHECKS = {
    "firmware": N_("Hypervisor Firmware"),
    "modules": N_("Kernel Drivers"),
    "device": N_("Virtualization Device"),
    "memlock": N_("Memory Lock Limit"),
    "hugepages": N_("Huge Pages"),
    "profile": N_("Device Profile"),
    "runtime": N_("Runtime Components"),
    "protected-share": N_("Protected Memory Sharing"),
    "graphics": N_("Graphics"),
    "sensors": N_("Battery and Temperature"),
}


def check_title(name):
    return _(CHECKS[name]) if name in CHECKS else name


def check_summary(name, good, detail):
    """One plain sentence for a diagnostic row; the raw detail stays available separately."""
    if name == "memlock":
        return _("Raised automatically when a virtual machine starts.")
    if name == "profile":
        return detail if good else _("Unknown device. Conservative limits are used.")
    if name == "graphics":
        modes = [m.strip() for m in detail.split(",") if m.strip() in GRAPHICS]
        return ", ".join(graphics(m) for m in modes) or _("None available")
    if good:
        return {
            "firmware": _("Gunyah is available."),
            "modules": _("Loaded."),
            "device": _("Ready to use."),
            "hugepages": _("Enabled."),
            "runtime": _("Installed."),
            "protected-share": _("Available."),
            "sensors": _("Readable."),
        }.get(name, _("OK."))
    if good is None:
        return {
            "firmware": _("Can't be read without administrator rights. This is normal."),
            "protected-share": _("Unknown until virtualization is available."),
            "sensors": _("Some sensors can't be read."),
        }.get(name, _("Unknown."))
    return {
        "firmware": _("This firmware doesn't provide Gunyah."),
        "modules": _("Not loaded. Use Load Drivers."),
        "device": _("Not accessible. Load the drivers, and use an account signed in on this device's screen."),
        "hugepages": _("Turned off. Virtual machines need shared-memory huge pages."),
        "runtime": _("Missing or incomplete. Reinstall GunyahVM."),
        "protected-share": _("Unavailable. Vulkan and native GPU modes are turned off."),
    }.get(name, _("Needs attention."))


# Backend messages are English and technical. Known causes get a sentence the
# user can act on; the original text is always shown under Details.
EXPLANATIONS = [
    (r"another (VM|crosvm) owned by this user is (running|still active)",
     N_("Another virtual machine is already running. Shut it down first.")),
    (r"already running or its VMM survived",
     N_("This virtual machine is still running in the background. Force it off, then start it again.")),
    (r"Battery below (\d+)%",
     lambda m: _("The battery is below {percent}%. Charge the device and try again.").format(percent=m[1])),
    (r"Device is throttling|Thermal shutdown",
     N_("The device is too hot. Let it cool down and try again.")),
    (r"Thermal guard unavailable|No passive thermal zones",
     N_("The device temperature can't be read, so virtual machines are blocked for safety.")),
    (r"Configured batteries source unavailable",
     N_("The battery level can't be read, so virtual machines are blocked for safety.")),
    (r"[Nn]eed (?:MemAvailable >= )?(\d+) MiB",
     lambda m: _("Not enough free memory ({size} needed). Close some apps or give the virtual machine less memory.").format(size=gigabytes(int(m[1])))),
    (r"Memory exceeds device/host budget",
     N_("The virtual machine has more memory than this device allows. Lower it in Settings.")),
    (r"vCPUs exceed",
     N_("The virtual machine has more processors than this device allows. Lower it in Settings.")),
    (r"Insufficient host RAM",
     N_("This device doesn't have enough memory for this system.")),
    (r"Not authorized|dismissed|polkit did not authorize|Request dismissed",
     N_("Permission was denied. Use an account signed in on this device's screen.")),
    (r"/dev/gunyah",
     N_("Virtualization isn't available right now. Open System Check to load the drivers.")),
    (r"Gunyah RM firmware unavailable|crosvm runtime is missing|runtime\.json|Runtime guest kernel is not installed",
     N_("Parts of GunyahVM are missing. Reinstall GunyahVM.")),
    (r"transparent hugepages|shmem THP",
     N_("Huge pages are turned off on this device. Open System Check for details.")),
    (r"display mode is unavailable",
     N_("The selected graphics mode isn't available on this device.")),
    (r"Stop the VM before",
     N_("Shut down the virtual machine first.")),
    (r"VM already exists",
     N_("A virtual machine with this name already exists.")),
    (r"Guest did not stop",
     N_("The virtual machine didn't shut down in time. You can force it off.")),
    (r"exited without confirmed clean shutdown",
     N_("The virtual machine stopped without a clean shutdown.")),
    (r"has not reported an (IP )?address",
     N_("The virtual machine has no network address yet. Wait until it has finished starting.")),
    (r"VNC loopback port is already in use",
     N_("The display port is already used by another program.")),
    (r"remmina",
     N_("The Remmina viewer isn't installed.")),
    (r"No space left|Errno 28",
     N_("There isn't enough free storage.")),
    (r"startup failed",
     N_("The virtual machine failed to start. The console may show why.")),
]


def explain(message):
    """A translated sentence for a known backend failure, or None."""
    for pattern, answer in EXPLANATIONS:
        match = re.search(pattern, message or "")
        if match:
            return answer(match) if callable(answer) else _(answer)
    return None
