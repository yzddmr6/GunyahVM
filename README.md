<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# GunyahVM

**Linux and Windows virtual machines on Qualcomm Gunyah, with a GTK desktop application and a shared command-line interface.** Created and maintained by yzddMr6. GunyahVM is not affiliated with Qualcomm.

[简体中文](README.zh-CN.md) · [Linux guide](docs/linux.md) · [Windows guide](docs/windows.md) · [Downloads](https://github.com/yzddmr6/GunyahVM/releases/latest)

## Direction

One application serves every supported device. Hardware capabilities are discovered at runtime; small DT-matched profiles contain resource limits and guard policies. crosvm, renderers and supervision run as your ordinary account. Distribution integration is separate from the VM core.

## Features

| Function | Availability |
| --- | --- |
| GTK4/libadwaita application and CLI | VM list, creation, settings, console and host information |
| Linux ARM64 | Cloud images or local disks, SSH, cloud-init and clean shutdown |
| Linux graphics | Software display; virgl, Venus and native modes appear only with the required host/runtime/guest capabilities |
| Windows 11 ARM64 | User-supplied ISO, WinPE/DISM deployment, software display over loopback VNC, key-only control and RDP on supporting editions |
| Shared folders and networking | Writable Linux 9p exports, NetworkManager NAT, optional vhost-net, loopback TCP/UDP forwards |
| Lifecycle and guards | Restart, bounded console replay, low-battery/temperature guards and shutdown before host sleep |

Windows firmware, drivers and preparation tools are included by default. No Windows ISO, licence, activation key or installed guest system is supplied. Closing the manager window leaves VMs running.

## Devices and requirements

| Device profile | DT compatible |
| --- | --- |
| Xiaomi Pad 8 Pro | `xiaomi,piano` |
| OnePlus Pad 2 Pro | `oneplus,erhai` |

Unknown boards use conservative defaults and report `UNKNOWN`; profiles never enable hardware features. Firmware must expose Gunyah RM information, and the host kernel must provide the Gunyah VM ABI. The Debian package targets Ubuntu ARM64 with GTK4/libadwaita, systemd/logind, udev, polkit and NetworkManager. Installing the application does not add missing kernel support.

Starts reserve the larger of 1 GiB or 25% of host RAM, cap locked memory at 6 GiB and guest memory at 5120 MiB, and check current free memory. Windows requires at least 4608 MiB guest RAM and a sparse disk of at least 64 GiB. Windows media preparation also keeps an 8 GiB free-space reserve.

## Install and start

Download the package from [Releases](https://github.com/yzddmr6/GunyahVM/releases/latest):

```sh
sudo apt install ./gunyahvm*_arm64.deb
gunyahvm
```

Use Host Information to load drivers. The installed logind delay configuration takes effect when logind next starts; restart the host after first installation. Active local desktop users receive device access automatically. An administrator can add a remote account to `gunyah`; log in again after the group change. See [security](docs/security.md).

```sh
gunyahvm check
gunyahvm load
gunyahvm create ubuntu --start
gunyahvm list
gunyahvm ssh ubuntu
gunyahvm stop ubuntu
```

The app generates an SSH key when none is supplied. `check --json` exports host capabilities. `check` distinguishes failed checks from unknown firmware/profile information; it does not load drivers or raise limits. A low shell memlock limit can be supplied to a new VM by the authorized launcher.

## Use and maintenance

VM data lives in `~/.local/share/gunyahvm/`; runtime locks live in `/run/user/UID/gunyahvm/`. Stop a VM before editing resources, sharing folders or deleting its disk. `stop --force` is a crash stop and can lose guest data. Sleep guards try clean shutdown first; timeout or forced termination is recorded as a failure, never as clean shutdown.

One VM runs per user. Audio, USB passthrough, live migration, Windows 3D and guest Secure Boot/vTPM are not provided. Windows uses test-signed drivers and Test Mode. Loopback VNC can be accessed by other local accounts. VM formats from older or other tools are not imported.

[Linux](docs/linux.md) · [Windows](docs/windows.md) · [New devices](docs/new-device.md)

## Development and sources

[Architecture](docs/architecture.md) · [Runtime component interface](docs/runtime-components.md) · [Building](docs/building.md) · [Contributing](CONTRIBUTING.md)

The application adapts **iceland-vms** (mzakamin-hub) UI, configuration, graphics, guest control and forwarding code; earlier GunyahVM provides ordinary-user launch, lifecycle, images, Windows deployment and packaging. **librevm** informs optional Chinese mirrors, console replay and the balloon guard. The runtime derives from iceland-gvm, DroidVM, crosvm/minijail, virglrenderer, Linux, Mesa, edk2 and virtio-win. Exact commits and retained notices are in [NOTICE](NOTICE) and [debian/copyright](debian/copyright).

The project is GPL-2.0-only; inherited components retain their original licences. Distribute corresponding source with binary packages. [Security reporting](SECURITY.md).
