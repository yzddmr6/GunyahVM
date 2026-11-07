<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# Changelog

## 1.0.0 — first release

- Run Linux ARM64 guests with cloud-image creation, persistent storage, SSH access and managed networking.
- Prepare and run Windows 11 ARM64 guests using your own ISO, included UEFI and drivers, and a Remmina VNC display.
- Install one `gunyahvm` ARM64 package with all Linux and Windows dependencies and host integration.
- Discover Gunyah from the live device tree, choose resource defaults from host memory, and grant access to active local-seat users or members of the `gunyah` group.
- Inspect the host with `check` and view authors, licences and acknowledgements with `about`. Firmware inspection prefers the readable device tree and reports `UNKNOWN` when a required raw FDT fallback cannot be read.
- Check device access and polkit authorization before creating a VM or downloading its image.
- Include matching English and Chinese guides for users, device integration and building from pinned sources and verified release inputs.
- Created and maintained by yzddMr6, originating in the Xiaomi Pad 8 Pro mainline port, with upstream attribution preserved.
