<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Changelog

## 1.1.0 — Redesigned interface

- A new interface built on current GNOME patterns: a sidebar of virtual machines and, for each one, a page with its state, the action that fits that state and an overview of its system, resources, graphics and network.
- The interface follows the system language, with complete English and Simplified Chinese translations; labels no longer show both languages at once.
- Creating a VM starts with choosing a system, then asks only for a name and resources with sensible defaults. Creation runs in the background, problems are flagged as you type, and Windows passwords are entered twice.
- Settings are grouped into General, Display, Network, Sharing and Advanced pages and saved as you change them. Shared folders are added with a folder picker and offer a copyable mount command; port forwards are added in a small dialog.
- System Check replaces Host information: it states whether the device can run virtual machines, what needs attention and how to fix it.
- Errors explain common causes in plain language and keep the original message under Details. While one VM runs, the others explain why they can't start instead of failing.
- While a Linux VM is still starting (the first start installs software; with a desktop this can take 20 minutes or more), its page says so; Shut Down explains why it has to wait and offers Force Off.
- Fixed: an Ubuntu VM created with graphics never finished its first start, because its desktop setup could not install the Firefox snap. The guest kernel (now 7.2.15-gvm63) mounts the compressed images snaps use, so the desktop installs completely. Ubuntu VMs created with graphics by 1.0.x did not finish that setup; delete and recreate them.
- Fixed: VMs with OpenGL acceleration closed as soon as they started on Xiaomi Pad 8 Pro system 1.2.0. The graphics sandbox blocked calls its Mesa makes (branch-protected code mappings and the shader cache); it now allows them, and writable code stays forbidden. These VMs also render on the tablet's GPU instead of in software: the sandbox now sees a host graphics driver that is installed outside the standard library directories.
- Fixed: in Ubuntu VMs every initramfs update failed with "Unsupported platform", which also broke the first-start desktop setup. New VMs are set up correctly; in an existing VM, run `echo linux,dummy-virt | sudo tee /etc/flash-kernel/machine` once.
- If first-start setup fails anyway, the VM still reports ready, so its network, port forwards and Shut Down keep working, and its page lists the steps that failed.
- Requires libadwaita 1.9 (Ubuntu 26.04).

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
