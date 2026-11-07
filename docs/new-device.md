<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# New-device integration

## Establish the platform

Start with a Qualcomm device whose firmware exposes Gunyah and whose kernel implements the ABI listed in the README. Keep an independent recovery method. Run `gunyahvm check` and report its reviewed output, root compatible strings, SoC, kernel version/configuration and firmware version. Do not publish serial numbers, account names, private paths or complete raw firmware dumps.

Inspect the live `/sys/firmware/devicetree/base` tree or `/sys/firmware/fdt`; never copy VMIDs, capability IDs or reserved addresses from another device. A published host VMID can be absent; new guest VMIDs are allocated by the resource manager. The command validates the hypervisor/resource-manager nodes and their two 64-bit capability IDs. Protected VM support and correct host firmware remain prerequisites.

## Device differences

Try defaults first. The two named devices require no shipped override. Only a proven resource ceiling may go in `/usr/share/gunyahvm/devices/<compatible>.conf`, owned by root and not writable by others:

```ini
[gunyahvm]
max_memory_mib = 3072
max_cpus = 2
```

The first matching root compatible selects the file. It can only lower limits at start; it cannot set VMIDs, capability IDs, commands, paths, firmware or another hypervisor. Those are not device configuration. Unknown keys and invalid ownership are rejected. Kernel ABI changes belong in the kernel or an explicit crosvm patch with origin and upstream status.

## Verify and report

First validate `check`, module loading, automatic local-seat permissions and the finite VMM limit. Confirm an ungrouped SSH login under a separate UID is denied and a grouped login works. Check a seat switch revokes future device opens. Then test Linux boot/readiness/SSH/NAT, stop/restart, kernel refresh and force-stop reporting. For Windows, test clean deployment handoff, OOBE, VNC reconnect, reset and shutdown. Stop before suspend or host reboot; check repeated starts do not leave TAPs or crosvm children. Submit the new-device issue template with evidence and limits; do not promote a device to verified from a successful offline build alone.
