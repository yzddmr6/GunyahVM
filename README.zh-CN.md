<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# GunyahVM

由 yzddMr6 创建并维护，源自小米平板 8 Pro 主线移植项目

[English](README.md) · [用户指南](docs/linux.zh-CN.md) · [Windows](docs/windows.zh-CN.md)

## 是什么

GunyahVM 是面向 ARM64 Linux 上 **Qualcomm Gunyah** 的 Linux 和 Windows 虚拟机命令行管理工具。唯一的 `gunyahvm` 包包含固定版本的 crosvm、管理命令、Windows 准备工具、UEFI 固件、签名来宾驱动及系统集成文件。**与 Qualcomm 无关联**。

## 能跑什么

- Linux ARM64 来宾：Ubuntu 云镜像、持久化磁盘、SSH、串口控制台和 NetworkManager NAT。
- Windows 11 ARM64：用户提供 ISO，使用 WinPE/DISM 安装、正常 OOBE，通过 Remmina 和本机 VNC 查看 1280×720 软件帧缓冲。
- Windows 支持及其依赖默认包含在同一个包中。不提供 Windows ISO、已安装系统、许可证、账户或激活密钥。

## 支持设备

| 支持程度 | 设备 |
| --- | --- |
| 支持 | 小米平板 8 Pro（SM8750） |
| 计划支持 | 一加平板 2 Pro（SM8750） |
| 按[新设备接入指南](docs/new-device.zh-CN.md)用 `gunyahvm check` 自查 | 其他高通 Gunyah 设备 |

设备 ID 和资源管理器能力 ID 取自实时固件设备树及内核资源管理器，应用不内置设备 ID 表。上述两台设备都不需要覆盖配置文件。

## 前提条件

Ubuntu 26.04 ARM64，使用 systemd/logind、udev、polkit 和 NetworkManager；固件提供 `gunyah-hypervisor` 与 `gunyah-resource-manager` 节点；内核实现对应的 Gunyah 用户态 ABI。设备名或 SoC 型号本身不代表兼容。

受支持驱动树需要：`CONFIG_GUNYAH=m`、`CONFIG_GUNYAH_HYPERCALLS=m`、`CONFIG_GUNYAH_TIMER=m`、`CONFIG_GUNYAH_PLATFORM_HOOKS=m`、`CONFIG_GUNYAH_IRQFD=m`、`CONFIG_GUNYAH_IOEVENTFD=m`、`CONFIG_ARM64=y`、`CONFIG_OF=y`、`CONFIG_SYSFS=y`、`CONFIG_USER_NS=y`、`CONFIG_SECCOMP=y`、`CONFIG_SECCOMP_FILTER=y`、`CONFIG_TUN=m`、`CONFIG_TRANSPARENT_HUGEPAGE=y`、`CONFIG_TMPFS=y`。核心会构建 `gunyah_rsc_mgr` 和 `gunyah_vcpu`，此驱动树没有这两个模块各自独立的 Kconfig 开关。受支持的 protected-without-firmware 路径使用 `CONFIG_GUNYAH_QCOM_PLATFORM=n` 和 `CONFIG_GUNYAH_QCOM_TRUSTED_VM=n`。可选 vhost 网络还需要 `CONFIG_VHOST_NET=m` 和管理员单独配置的设备权限。仅安装此包不能让普通发行版内核自动具备 Gunyah。

内存默认值按总内存和可用 CPU 推算；每次启动至少为宿主保留 1 GiB 和总内存的 25%，每进程最多锁定 6 GiB，来宾最多使用 5120 MiB。Windows 至少需要 4608 MiB 来宾内存，加上 256 MiB DMA 池与宿主余量。启动时还会检查当前可用内存。Windows 所在 ext4 或 XFS 文件系统至少留出 40 GiB 空间；系统盘是 64 GiB 稀疏文件。

## 安装

```sh
sudo apt install ./gunyahvm_1.0.0_arm64.deb
```

活动本地座席的桌面用户自动获得设备访问权；`gunyahvm start` 为新虚拟机进程取得有限的内存锁定额度，不改桌面会话本身的额度。SSH 和其他账户需要管理员加入组：

```sh
sudo usermod -aG gunyah "$USER"
```

修改组后重新登录。[安全模型](docs/security.zh-CN.md) 说明了按 UID 授权的 ACL 边界及权限助手。

## 快速开始

使用已有 SSH 公钥，或执行 `ssh-keygen -t ed25519` 创建。

```sh
gunyahvm about
gunyahvm check
gunyahvm load
gunyahvm create ubuntu --start
gunyahvm list
gunyahvm ssh ubuntu
gunyahvm stop ubuntu
```

`check` 只读取状态，逐项报告固件、模块、设备权限、当前内存锁定额度和大页，并给出修复提示。授权启动器给虚拟机设置额度之前，当前 shell 的 memlock 较低是正常现象；检查命令不会提升额度。固件节点对普通用户不可读时，可用 `sudo gunyahvm check` 进行同样的只读检查。

```sh
gunyahvm create windows --os windows11 --iso ~/Downloads/Windows11_ARM64.iso --start
gunyahvm view windows
```

选择 ISO 中提供的版本，完成 OOBE；在 Windows 内关机后执行 `gunyahvm stop windows`。数据只使用 `~/.local/share/gunyahvm/`，运行锁只使用 `/run/user/UID/gunyahvm/`。[Linux 指南](docs/linux.zh-CN.md) · [Windows 指南](docs/windows.zh-CN.md)。

## 限制

每用户同时运行一台虚拟机。不提供 GPU 加速、音频、USB 直通、在线迁移或挂起支持；运行时保持宿主唤醒，宿主关机前先停止来宾。不提供 Linux 来宾桌面集成。Windows 没有 vTPM 和 Secure Boot，安装检查被绕过，测试签名驱动要求 Test Mode。本机 VNC 不隔离其他本地账户。包不含来宾系统镜像。不识别或迁移其他工具的已有虚拟机数据。

## 作者与来源

**由 yzddMr6 创建并维护** — [项目主页](https://github.com/yzddmr6/GunyahVM)。

感谢 [iceland-gvm（mzakamin-hub）](https://github.com/mzakamin-hub/iceland-gvm)、[DroidVM（Droid-VM）](https://github.com/Droid-VM)、[crosvm](https://chromium.googlesource.com/crosvm/crosvm/) 和 [minijail](https://android.googlesource.com/platform/external/minijail/)（ChromiumOS Authors）、[edk2](https://github.com/tianocore/edk2)、[edk2-gunyah](https://github.com/Droid-VM/edk2-gunyah) 以及 [virtio-win](https://github.com/virtio-win/kvm-guest-drivers-windows)。固定提交、原始声明和组件许可证见 [NOTICE](NOTICE)、[DEP-5 copyright](debian/copyright) 及 `third_party/`。

## 许可证

整体为 **GPL-2.0-only**，因为命令行核心改编自 iceland-gvm 的 GPL-2.0-only `gvm/gvm.sh`。第三方组件及继承的 MIT 助手保留各自许可证和版权行。分发二进制包时同时提供对应源码包；参见[源码构建](docs/building.zh-CN.md)。

## 贡献

阅读 [CONTRIBUTING](CONTRIBUTING.zh-CN.md)，使用 DCO `Signed-off-by`，报告问题时附经检查的 `gunyahvm check` 输出。[新设备](docs/new-device.zh-CN.md)、[架构](docs/architecture.zh-CN.md)、[安全问题报告](SECURITY.zh-CN.md)。

路线：1.1 计划基于 iceland-vms 分支提供图形界面。
