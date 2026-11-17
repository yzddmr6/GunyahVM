<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Changelog

## 1.4.0 — Using the whole tablet

- Virtual machines no longer need **Load Drivers** first: starting or creating a VM loads the drivers the running kernel already has. **Load Drivers** in System Check still loads them without starting a VM.
- New VMs get all of the host's processors by default, and memory can go up to what the host currently has available; the fixed host reserve and the per-device memory and processor ceilings are gone. New Linux VMs start with about half of the available memory.
- Native GPU mode works on Xiaomi Pad 8 Pro and OnePlus Pad 2 Pro: the guest's Turnip and freedreno now recognize their Adreno 830. New Ubuntu VMs use this mode by default where it is available; virgl stays available.
- In native mode, programs started with `gunyahvm-graphics-run --native` use freedreno OpenGL on the Adreno 830; Vulkan programs use Turnip.
- The guest desktop logs into the session of the selected graphics mode. Before, automatic login always started the plain Ubuntu session, so Vulkan programs in a native desktop did not get Turnip.
- Guest displays follow the panel's refresh rate (120 Hz) for Linux, Windows and macOS. Linux guest frames are presented at the pace the application window draws, and an unchanged screen no longer wakes the host on a timer.
- Linux and Windows disks use io_uring with several workers, and new VMs with NAT networking use vhost-net when the host provides it, Windows included.
- Battery level and CPU throttling no longer block or stop VMs, and neither does a sensor that can't be read. Only a monitored zone reaching its kernel hot trip (critical when there is no hot trip) declines a start or requests a clean guest shutdown.
- Existing Linux VMs pick up this version's guest scripts at their next boot. Only GunyahVM's own files are updated; accounts, packages and your configuration are left alone.
- Linux desktop guests no longer wait about 2 minutes for the network at boot: when NetworkManager manages the network, the wait service that only applies to systemd-networkd is no longer enabled.
- Idle macOS VMs take much less host CPU: their processors wait for interrupts instead of spinning. VMs created by 1.3.0 get the corrected boot arguments at their next start, without changes to their disk.
- The macOS boot kit takes a newer Metal plugin and adds shader-cache warm-up, and the virtual GPU reports completed work by interrupt.

## 1.3.0 — macOS from Apple's servers, one package

- Create macOS 27.2 VMs from zero on the tablet: **+ → macOS** (or `gunyahvm create-macos`) downloads only the 7 files it needs from Apple's servers (about 13 GB), decrypts and assembles them locally, and the first boot reaches the login window. Creation shows progress, can be paused and resumed, and publishes the VM only when it is complete.
- Every new macOS VM gets its own control key and your own administrator account; only a salted password hash is kept while a creation is paused.
- A temporary Linux helper VM writes the macOS APFS disk, so the host kernel needs no APFS support and no administrator password is asked.
- GunyahVM is now a single `gunyahvm` package with the runtime, the Linux guest kernel and Mesa and the macOS boot kit built in; upgrading replaces the former `gunyahvm-runtime`, `gunyahvm-guest-kernel` and `gunyahvm-guest-mesa` packages.
- Existing macOS, Ubuntu and Windows VMs keep their disks, configuration and keys across the upgrade; a macOS boot bundle assembled earlier can still be imported.
- Host memory is prepared before macOS starts (clean page cache is dropped and memory compacted), so its boot memory gets enough contiguous 2 MiB blocks right after a creation or after other VMs have run.
- The macOS GPU renderer no longer writes a trace line for every GPU submission, so long macOS sessions stop growing a log in the runtime directory.
- The macOS host runtime is built from locked public sources, ships the licence texts of every linked component, and its sources are in the corresponding-source archive.

## 1.2.0 — Embedded display and everyday VM controls

- Use Linux and Windows displays inside GunyahVM with proportional scaling, fullscreen and display reconnection.
- Linux supports on-demand desktop clipboard text and read-only or writable virtiofs folders.
- Take and restore disk snapshots or clone an installed Linux or Windows VM while it is shut down.
- Linux guests request DHCP on a stable interface name. Host graphics detection queries the DRM driver interface.

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
