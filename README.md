<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# GunyahVM

**Linux virtual machines on Qualcomm Gunyah, with a GTK desktop application and a shared command-line interface.** Created and maintained by yzddMr6. GunyahVM is not affiliated with Qualcomm.

[简体中文](README.zh-CN.md) · [Linux guide](docs/linux.md) · [Downloads](https://github.com/yzddmr6/GunyahVM/releases/latest)

## Direction

One application serves every supported device. Hardware capabilities are discovered at runtime; small DT-matched profiles contain resource limits and guard policies. crosvm, renderers and supervision run as your ordinary account. Distribution integration is separate from the VM core.

## Features

| Function | Availability |
| --- | --- |
| GTK4/libadwaita application and CLI | Create a Linux ARM64 VM from a local disk image, start it and shut it down cleanly |
| Linux software display | Desktop window; reopen the manager while the VM keeps running |
| Linux graphics | Guest GL acceleration (virgl) |

Closing the manager window leaves VMs running.

## Devices and requirements

| Device profile | DT compatible |
| --- | --- |
| Xiaomi Pad 8 Pro | `xiaomi,piano` |
| OnePlus Pad 2 Pro | `oneplus,erhai` |

Unknown boards use conservative defaults and report `UNKNOWN`; profiles never enable hardware features. Firmware must expose Gunyah RM information, and the host kernel must provide the Gunyah VM ABI. The Debian package targets Ubuntu ARM64 with GTK4/libadwaita, systemd/logind, udev, polkit and NetworkManager. Installing the application does not add missing kernel support.

Starts reserve the larger of 1 GiB or 25% of host RAM, cap locked memory at 6 GiB and guest memory at 5120 MiB, and check current free memory.

## Install and start

Download the package from [Releases](https://github.com/yzddmr6/GunyahVM/releases/latest):

```sh
sudo apt install ./gunyahvm*_arm64.deb
gunyahvm
```

Use Host Information to load drivers. Active local desktop users receive device access automatically. An administrator can add a remote account to `gunyah`; log in again after the group change. See [security](docs/security.md).

Choose a local ARM64 disk image and software display in the creation wizard, then create and start the VM. See the usage guides for CLI options.

At GUI startup, GunyahVM contacts GitHub to check for updates without sending device information; disable this in Preferences or with `GUNYAHVM_NO_UPDATE_CHECK=1`.

## Use and maintenance

VM data lives in `~/.local/share/gunyahvm/`; runtime locks live in `/run/user/UID/gunyahvm/`. Stop a VM before editing resources, sharing folders or deleting its disk. `stop --force` is a crash stop and can lose guest data.

One VM runs per user. Audio, USB passthrough, live migration, and guest Secure Boot/vTPM are not provided. Loopback VNC can be accessed by other local accounts. VM formats from older or other tools are not imported.

[Linux](docs/linux.md) · [New devices](docs/new-device.md)

## Development and sources

[Architecture](docs/architecture.md) · [Runtime component interface](docs/runtime-components.md) · [Building](docs/building.md) · [Contributing](CONTRIBUTING.md)

The application adapts **iceland-vms** (mzakamin-hub) UI, configuration, graphics, guest control and forwarding code; earlier GunyahVM provides ordinary-user launch, lifecycle, images and packaging. **librevm** informs optional Chinese mirrors, console replay and the balloon guard. The runtime derives from iceland-gvm, DroidVM, crosvm/minijail, virglrenderer, Linux, Mesa, edk2 and virtio-win. Exact commits and retained notices are in [NOTICE](NOTICE) and [debian/copyright](debian/copyright).

The project is GPL-2.0-only; inherited components retain their original licences. Distribute corresponding source with binary packages. [Security reporting](SECURITY.md).
