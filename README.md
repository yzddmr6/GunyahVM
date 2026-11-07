<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# GunyahVM

由 yzddMr6 创建并维护，源自小米平板 8 Pro 主线移植项目

Created and maintained by **yzddMr6**, originating in the Xiaomi Pad 8 Pro mainline port.
[简体中文](README.zh-CN.md) · [User guides](docs/linux.md) · [Windows](docs/windows.md)

## What it is

GunyahVM is a command-line Linux and Windows VM manager for **Qualcomm Gunyah** on ARM64 Linux. One `gunyahvm` package contains the pinned crosvm build, management tools, Windows preparation tools, UEFI firmware, signed guest drivers and host integration. GunyahVM is not affiliated with Qualcomm. **与 Qualcomm 无关联**。

## What runs

- Linux ARM64 guests: Ubuntu cloud images, persistent disks, SSH, serial console and NetworkManager NAT.
- Windows 11 ARM64: owner-supplied ISO, WinPE/DISM installation, normal OOBE and a 1280×720 software framebuffer viewed through Remmina over loopback VNC.
- Windows support and its dependencies are included in the single package by default. No Windows ISO, installed system, licence, account or activation key is supplied.

## Devices

| Support | Device |
| --- | --- |
| Supported | Xiaomi Pad 8 Pro (SM8750) |
| Planned | OnePlus Pad 2 Pro (SM8750) |
| Follow the [new-device guide](docs/new-device.md) and run `gunyahvm check` | Other Qualcomm Gunyah devices |

Device IDs and resource-manager capability IDs come from the live firmware device tree and kernel resource manager. No device-ID table is built into the application. Neither named device needs an override file.

## Requirements

Ubuntu 26.04 ARM64 with systemd/logind, udev, polkit and NetworkManager; firmware exposing `gunyah-hypervisor` and `gunyah-resource-manager` nodes; a kernel with the corresponding Gunyah userspace ABI. A device name or SoC alone does not establish compatibility.

Required kernel settings in the supported driver tree: `CONFIG_GUNYAH=m`, `CONFIG_GUNYAH_HYPERCALLS=m`, `CONFIG_GUNYAH_TIMER=m`, `CONFIG_GUNYAH_PLATFORM_HOOKS=m`, `CONFIG_GUNYAH_IRQFD=m`, `CONFIG_GUNYAH_IOEVENTFD=m`, `CONFIG_ARM64=y`, `CONFIG_OF=y`, `CONFIG_SYSFS=y`, `CONFIG_USER_NS=y`, `CONFIG_SECCOMP=y`, `CONFIG_SECCOMP_FILTER=y`, `CONFIG_TUN=m`, `CONFIG_TRANSPARENT_HUGEPAGE=y`, `CONFIG_TMPFS=y`. The core builds `gunyah_rsc_mgr` and `gunyah_vcpu`; there are no separate Kconfig symbols for those in this tree. Use `CONFIG_GUNYAH_QCOM_PLATFORM=n` and `CONFIG_GUNYAH_QCOM_TRUSTED_VM=n` for the supported protected-without-firmware path. Optional vhost networking needs `CONFIG_VHOST_NET=m` and separately administered device permissions. Gunyah is not enabled by a generic stock kernel merely by installing this package.

RAM defaults are calculated from total memory and available CPUs. Each launch reserves at least 1 GiB and 25% of total host RAM, with at most 6 GiB locked per process and 5120 MiB guest RAM. Windows needs at least 4608 MiB guest RAM plus its 256 MiB DMA pool and host reserve. Start also checks current available memory. For Windows, keep at least 40 GiB free on ext4 or XFS; its 64 GiB disk is sparse.

## Install

```sh
sudo apt install ./gunyahvm_1.0.0_arm64.deb
```

An active local-seat desktop user obtains device access automatically; `gunyahvm start` acquires a finite memory-lock limit for the new VM process without changing the desktop session's limit. SSH and other accounts require administrator-assigned group membership:

```sh
sudo usermod -aG gunyah "$USER"
```

Log in again after a group change. See [security](docs/security.md) for the UID-based ACL boundary and privilege helpers.

## Quick start

Use an existing SSH public key, or create one with `ssh-keygen -t ed25519`.

```sh
gunyahvm about
gunyahvm check
gunyahvm load
gunyahvm create ubuntu --start
gunyahvm list
gunyahvm ssh ubuntu
gunyahvm stop ubuntu
```

`check` only reads state and reports firmware, modules, device access, the current memory-lock limit and hugepages, with a repair hint for each. A low shell memlock result is expected before the authorized launcher sets the VM's limit; it never changes limits during inspection. Firmware inspection first uses `/sys/firmware/devicetree/base`; only missing required nodes trigger the raw FDT fallback. An unreadable fallback reports `UNKNOWN` (unable to confirm), and `sudo gunyahvm check` can inspect it read-only. Exit codes are 0 for all PASS, 1 if any item fails, and 2 for an incomplete check with UNKNOWN but no FAIL.

```sh
gunyahvm create windows --os windows11 --iso ~/Downloads/Windows11_ARM64.iso --start
gunyahvm view windows
```

Choose the edition offered by your ISO, complete OOBE, then shut down inside Windows before `gunyahvm stop windows`. Data lives only in `~/.local/share/gunyahvm/`; runtime locks live in `/run/user/UID/gunyahvm/`. [Linux guide](docs/linux.md) · [Windows guide](docs/windows.md).

## Limits

One running VM per user. No GPU acceleration, audio, USB passthrough, live migration or suspend support; keep the host awake and stop guests before host shutdown. Linux guest desktop integration is not provided. Windows has no vTPM or Secure Boot; installation checks are bypassed and test-signed drivers require Test Mode. Loopback VNC is not isolated from other local accounts. The package contains no guest images. Existing VM data from other tools is not recognized or migrated.

## Authors and sources

**Created and maintained by yzddMr6** — [project homepage](https://github.com/yzddmr6/GunyahVM).

Thanks to [iceland-gvm (mzakamin-hub)](https://github.com/mzakamin-hub/iceland-gvm), [DroidVM (Droid-VM)](https://github.com/Droid-VM), [crosvm](https://chromium.googlesource.com/crosvm/crosvm/) and [minijail](https://android.googlesource.com/platform/external/minijail/) (ChromiumOS Authors), [edk2](https://github.com/tianocore/edk2), [edk2-gunyah](https://github.com/Droid-VM/edk2-gunyah), and [virtio-win](https://github.com/virtio-win/kvm-guest-drivers-windows). Exact pins, original notices and component licences are in [NOTICE](NOTICE), [DEP-5 copyright](debian/copyright), and `third_party/`.

## Licence

The overall project is **GPL-2.0-only**, because its command-line core adapts iceland-gvm's GPL-2.0-only `gvm/gvm.sh`. Third-party components and inherited MIT helpers retain their own licences and copyright notices. Distribute the corresponding-source archive with the binary package; see [building](docs/building.md).

## Contributing

Read [CONTRIBUTING](CONTRIBUTING.md), use DCO `Signed-off-by`, and report bugs with reviewed `gunyahvm check` output. [New devices](docs/new-device.md), [architecture](docs/architecture.md), and [security reporting](SECURITY.md).

Roadmap: 1.1 plans a graphical interface based on an iceland-vms branch.
