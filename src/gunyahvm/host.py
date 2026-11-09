# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Read-only Gunyah discovery and finite, memory-derived resource policy."""

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import resource
import struct
from . import __version__

MODULES = ("gunyah_rsc_mgr", "gunyah_vcpu", "gunyah_irqfd", "gunyah_ioeventfd")
DT_ROOT = Path("/sys/firmware/devicetree/base")
FDT = Path("/sys/firmware/fdt")
THP = Path("/sys/kernel/mm/transparent_hugepage/shmem_enabled")
DEVICE_DIR = Path("/usr/share/gunyahvm/devices")
MIB = 1024**2


def memory_budget(total_mib=None):
    if total_mib is None:
        fields = dict(
            line.split(":", 1)
            for line in Path("/proc/meminfo").read_text().splitlines()
        )
        total_mib = int(fields["MemTotal"].split()[0]) // 1024
    # Preserve at least 1 GiB (and 25% on larger machines). Limit each process
    # to at most 6 GiB, preserving the tested Gunyah DMA/addressing envelope.
    reserve = max(1024, total_mib // 4)
    locked = max(0, min(6144, total_mib - reserve))
    return {
        "memlock_mib": locked,
        "host_reserve_mib": reserve,
        "guest_max_mib": max(0, min(5120, locked - 256)),
    }


def defaults(guest_os, total_mib=None, cpu_count=None):
    budget = memory_budget(total_mib)
    cpus = cpu_count or len(os.sched_getaffinity(0))
    minimum = 4608 if guest_os == "windows11" else 512
    desired = (
        4608 if guest_os == "windows11" else min(2048, budget["guest_max_mib"] // 2)
    )
    if budget["guest_max_mib"] < minimum:
        raise ValueError(
            f"Insufficient host RAM: {guest_os} needs at least {minimum} MiB plus host/DMA reserves"
        )
    return {
        "memory_mib": max(minimum, desired // 256 * 256),
        "cpus": min(4 if guest_os == "windows11" else 2, cpus),
    }


def strings(value):
    return [item.decode("ascii") for item in value.rstrip(b"\0").split(b"\0") if item]


def parse_fdt(data):
    """Parse only node names and properties; never expose unrelated properties."""
    if len(data) < 40:
        raise ValueError("truncated FDT header")
    (
        magic,
        size,
        off_struct,
        off_strings,
        _,
        version,
        _,
        _,
        size_strings,
        size_struct,
    ) = struct.unpack_from(">10I", data)
    if magic != 0xD00DFEED or version < 17 or size > len(data) or size > 16 * MIB:
        raise ValueError("invalid FDT header")
    if off_struct + size_struct > size or off_strings + size_strings > size:
        raise ValueError("FDT section outside buffer")
    names = data[off_strings : off_strings + size_strings]
    pos, end, stack, nodes = off_struct, off_struct + size_struct, [], {}
    while pos + 4 <= end:
        token = struct.unpack_from(">I", data, pos)[0]
        pos += 4
        if token == 1:
            stop = data.find(b"\0", pos, end)
            if stop < 0:
                raise ValueError("unterminated FDT node")
            stack.append(data[pos:stop].decode("ascii"))
            nodes["/" + "/".join(stack[1:])] = {}
            pos = (stop + 4) & ~3
        elif token == 2:
            if not stack:
                raise ValueError("unbalanced FDT node")
            stack.pop()
        elif token == 3:
            if not stack or pos + 8 > end:
                raise ValueError("invalid FDT property")
            length, offset = struct.unpack_from(">II", data, pos)
            pos += 8
            stop = names.find(b"\0", offset)
            if offset >= len(names) or stop < 0 or pos + length > end:
                raise ValueError("invalid FDT property bounds")
            name = names[offset:stop].decode("ascii")
            nodes["/" + "/".join(stack[1:])][name] = data[pos : pos + length]
            pos = (pos + length + 3) & ~3
        elif token == 4:
            continue
        elif token == 9 and not stack:
            return nodes
        else:
            raise ValueError("invalid FDT token or nesting")
    raise ValueError("FDT has no end token")


def read_tree(root):
    nodes = {}
    if not root.is_dir():
        raise FileNotFoundError("live device tree is absent")
    # Read only properties relevant to Gunyah; never serial-number, MAC or keys.
    for p in [root, *root.rglob("*")]:
        if p.is_dir():
            values = {}
            for name in (
                "compatible",
                "status",
                "reg",
                "qcom,vmid",
                "vmid",
                "#address-cells",
                "#size-cells",
                "interrupts",
            ):
                prop = p / name
                if prop.is_file():
                    values[name] = prop.read_bytes()
            nodes["/" if p == root else "/" + str(p.relative_to(root))] = values
    return nodes


@dataclass
class Firmware:
    compatible: list
    vmids: dict
    capability_ids: dict
    hypervisor: bool
    source: str
    interrupts: dict = field(default_factory=dict)

    def require(self):
        if not self.hypervisor or not self.capability_ids:
            raise ValueError(
                "Missing Gunyah hypervisor/resource-manager nodes; boot firmware and a kernel exposing the live Gunyah DT"
            )
        return self

    def summary(self):
        return {
            "compatible": self.compatible,
            "vmids": self.vmids,
            "capability_ids": self.capability_ids,
            "hypervisor": self.hypervisor,
            "source": self.source,
            "interrupts": self.interrupts,
        }


def firmware_from_nodes(nodes, source):
    vmids, capabilities, interrupts = {}, {}, {}
    hypervisor = False
    for name, properties in nodes.items():
        if properties.get("status", b"okay\0").rstrip(b"\0") not in (b"okay", b"ok"):
            continue
        compat = strings(properties.get("compatible", b""))
        if "gunyah-hypervisor" in compat or "qcom,gunyah-hypervisor" in compat:
            hypervisor = True
        if any(
            x in compat
            for x in ("gunyah-resource-manager", "qcom,gunyah-resource-manager")
        ):
            reg = properties.get("reg", b"")
            if len(reg) != 16:
                raise ValueError(
                    "Gunyah resource manager must expose two 64-bit capability IDs"
                )
            capabilities[name] = list(struct.unpack(">QQ", reg))
            irq = properties.get("interrupts", b"")
            if len(irq) % 4:
                raise ValueError("RM interrupts must contain 32-bit cells")
            interrupts[name] = list(struct.unpack(">" + "I" * (len(irq) // 4), irq))
        if name.startswith("/hypervisor/"):
            for prop in ("qcom,vmid", "vmid"):
                if prop in properties:
                    value = properties[prop]
                    if len(value) != 4:
                        raise ValueError("VMID must be a big-endian 32-bit cell")
                    vmids[name + ":" + prop] = int.from_bytes(value, "big")
    return Firmware(
        strings(nodes.get("/", {}).get("compatible", b"")),
        vmids,
        capabilities,
        hypervisor,
        source,
        interrupts,
    )


class FirmwareUnavailable(OSError):
    """Live firmware could not be inspected; this does not prove it is absent."""


def firmware(root=DT_ROOT, fdt=FDT):
    tree_error = None
    result = None
    try:
        result = firmware_from_nodes(read_tree(root), "live tree")
        if result.hypervisor and result.capability_ids:
            return result
    except OSError as exc:
        tree_error = exc
    # Only consult raw FDT when the readable tree cannot supply the required
    # nodes. Some systems hide /hypervisor from the exported tree, while the
    # raw kernel-owned FDT is restricted to root. Neither implies no Gunyah.
    try:
        data = fdt.read_bytes()
    except OSError as exc:
        # physical-platform may mask hypervisor nodes in the exported tree.
        # The only fallback for an ordinary caller is a fixed, read-only action;
        # test roots and other paths must never reach the privileged helper.
        if (isinstance(exc, PermissionError) and os.geteuid() != 0
                and root == DT_ROOT and fdt == FDT):
            from . import privilege
            try:
                summary = privilege.firmware_info()
            except OSError as helper_error:
                raise FirmwareUnavailable(str(helper_error)) from helper_error
            return Firmware(result.compatible if result is not None else [], {},
                            summary["capability_ids"], summary["hypervisor"],
                            "read-only privileged FDT summary")
        detail = f"raw FDT is unreadable: {exc}"
        if tree_error is not None:
            detail = f"live tree is unreadable: {tree_error}; " + detail
        else:
            detail = "required nodes not found in exported live tree; " + detail
        raise FirmwareUnavailable("Unable to confirm / 无法确认: " + detail) from exc
    fallback = firmware_from_nodes(parse_fdt(data), "live FDT")
    # ABL may overwrite the original FDT identity. The kernel restores the
    # board in the exported live tree even when /hypervisor is masked there.
    # Fall back only for RM data; retain the authoritative live board profile.
    if result is not None and result.compatible:
        fallback.compatible = result.compatible
    return fallback


def device_options(compatible, directory=DEVICE_DIR):
    from .devices import match

    profile = match(compatible, directory)
    return profile["resources"] if profile["id"] != "unknown" else {}


def about():
    return f"""GunyahVM {__version__}
Created and maintained by yzddMr6 <yzddmr6@gmail.com>
由 yzddMr6 创建并维护
Homepage: https://github.com/yzddmr6/GunyahVM
License: GPL-2.0-only; third-party components retain their own licences.
Thanks: iceland-gvm (mzakamin-hub); DroidVM (Droid-VM);
        crosvm and minijail (ChromiumOS Authors); edk2 and edk2-gunyah;
        virtio-win (Red Hat and contributors).
GunyahVM is not affiliated with Qualcomm. 与 Qualcomm 无关联。
See /usr/share/doc/gunyahvm/copyright and NOTICE for provenance."""


def diagnostic_rows(root=Path("/"), limits=None):
    rows = []

    def item(name, ok, detail, fix):
        rows.append((name, None if ok is None else bool(ok), detail, fix))

    try:
        fw = firmware(root / "sys/firmware/devicetree/base", root / "sys/firmware/fdt")
        item(
            "firmware",
            fw.hypervisor and bool(fw.capability_ids),
            json.dumps(fw.summary(), ensure_ascii=False),
            "Use firmware exposing Gunyah hypervisor and resource-manager nodes; do not copy another device's IDs.",
        )
    except FirmwareUnavailable as exc:
        item(
            "firmware",
            None,
            str(exc),
            "Run sudo gunyahvm check for a read-only raw FDT inspection; unreadable firmware does not mean Gunyah is absent.",
        )
    except (OSError, ValueError) as exc:
        item(
            "firmware",
            False,
            str(exc),
            "Expose the live firmware DT; if it is access-restricted, run sudo gunyahvm check for this read-only inspection.",
        )
    missing = [m for m in MODULES if not (root / "sys/module" / m).exists()]
    item(
        "modules",
        not missing,
        "missing: " + ", ".join(missing) if missing else "all required drivers present",
        "Run gunyahvm load; install a kernel with CONFIG_GUNYAH, GUNYAH_IRQFD and GUNYAH_IOEVENTFD if unavailable.",
    )
    dev = root / "dev/gunyah"
    item(
        "device",
        dev.is_char_device() and os.access(dev, os.R_OK | os.W_OK),
        str(dev),
        "Run gunyahvm load; use an active local seat, or have an administrator add your SSH account to gunyah and log in again.",
    )
    try:
        soft, hard = (
            limits
            if limits is not None
            else resource.getrlimit(resource.RLIMIT_MEMLOCK)
        )
        budget = memory_budget()
        required = budget["memlock_mib"] * MIB
        sufficient = soft == resource.RLIM_INFINITY or soft >= required
        item(
            "memlock",
            sufficient,
            f"current soft={soft} hard={hard}; authorized launch budget={required} bytes",
            "Use gunyahvm start: its polkit launcher supplies this finite limit before dropping privilege; check does not raise limits.",
        )
    except (OSError, ValueError, KeyError) as exc:
        item(
            "memlock",
            False,
            str(exc),
            "Mount procfs to inspect the host memory budget, then use gunyahvm start for its finite authorized limit.",
        )
    try:
        value = (root / str(THP).lstrip("/")).read_text().strip()
        good = bool(re.search(r"\[(advise|always|within_size)\]", value))
        item(
            "hugepages",
            good,
            value,
            "Enable shmem THP with sudo systemd-tmpfiles --create gunyahvm-memory.conf; enable CONFIG_TRANSPARENT_HUGEPAGE in the kernel.",
        )
    except OSError as exc:
        item(
            "hugepages",
            False,
            str(exc),
            "Install a kernel with CONFIG_TRANSPARENT_HUGEPAGE and enable shmem THP.",
        )
    return rows


def check():
    rows = diagnostic_rows()
    for name, good, detail, fix in rows:
        status = "UNKNOWN" if good is None else ("PASS" if good else "FAIL")
        print(f"{status} {name}: {detail}")
        print("  Fix if needed: " + fix)
    if any(row[1] is False for row in rows):
        return 1
    return 2 if any(row[1] is None for row in rows) else 0
