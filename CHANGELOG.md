<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Changelog

## 1.0.0 — first release

- GTK4/libadwaita VM management and CLI share one validated core and ordinary-user supervisor. Closing the app leaves guests running.
- Discover firmware, Gunyah extensions, GPU access and runtime components; DT-matched device profiles supply only ceilings, sensor sources and guard policies.
- Offer Linux graphics modes only when their host/runtime/guest requirements are present. Support writable 9p folders, optional vhost-net and loopback forwarding.
- Prepare Linux cloud images with official checksum verification and optional Chinese mirrors; provide cloud-init, a guest agent and bounded console replay.
- Deploy Windows from a user-supplied ARM64 ISO with bundled UEFI/drivers, VNC, key-only guest control, RDP and explicit clean/forced shutdown reporting.
- Use one fixed-action polkit helper, finite memlock before privilege drop, UID-derived networks and a logind delay inhibitor for sleep.
- Ship an ARM64 application with exactly matched crosvm/virglrenderer, guest kernel and guest Mesa packages, runtime manifest contract, retained source attribution, offline regression tests and English/Chinese guides.
