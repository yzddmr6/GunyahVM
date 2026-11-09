<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Changelog

## 1.0.1 — Windows guests

- Deploy offline from your own Windows 11 ARM64 ISO, create a local account through the application and use software display with local VNC.
- Configure OpenSSH from the installation media for key-authenticated guest control and clean shutdown; hide the control account from Windows sign-in.
- Complete offline local-account setup and read Windows PowerShell installer exit status reliably.
- Redesign bilingual usage and build guides, including Windows deployment, display and network limits.

- Follow the device's thermal trip points for temperature protection.

## 1.0.0 — first release

- Linux ARM64 virtual machines through the GTK4/libadwaita application and CLI: create from a local disk image, start and shut down cleanly. Closing and reopening the manager reconnects to running guests.
- Software display and guest GL acceleration (virgl).
- Check for new releases asynchronously at GUI startup, with a 24-hour cache, an optional update banner and a settings/environment opt-out.
