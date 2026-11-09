<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# GunyahVM

**在 Qualcomm Gunyah 上运行 Linux 虚拟机，提供 GTK 桌面应用和共用核心的命令行。** 由 yzddMr6 创建并维护，与 Qualcomm 无关联。

[English](README.md) · [Linux 指南](docs/linux.zh-CN.md) · [下载](https://github.com/yzddmr6/GunyahVM/releases/latest)

## 路线选择

所有设备共用一个应用。硬件能力由运行时探测，按 DT 匹配的小型设备档案只保存资源上限和守护策略。crosvm、渲染器和监督进程都以普通账户运行，发行版集成与虚拟机核心分离。

## 功能

| 功能 | 提供内容 |
| --- | --- |
| GTK4/libadwaita 界面与 CLI | 创建本地 Linux ARM64 虚拟机、启动与干净关机 |
| Linux 软件显示 | 桌面窗口；管理器关闭再打开后继续管理运行中的虚拟机 |
| Linux 图形 | 来宾 GL 加速（virgl） |

关闭管理器窗口不会停止虚拟机。

## 设备与要求

| 设备档案 | DT compatible |
| --- | --- |
| 小米平板 8 Pro | `xiaomi,piano` |
| 一加平板 2 Pro | `oneplus,erhai` |

未知设备使用保守默认值，并报告 `UNKNOWN`；档案不会开启硬件能力。固件须提供 Gunyah RM 信息，宿主内核须实现 Gunyah VM ABI。deb 面向提供 GTK4/libadwaita、systemd/logind、udev、polkit 和 NetworkManager 的 Ubuntu ARM64。安装应用不会补上缺失的内核支持。

启动时保留 1 GiB 与宿主总内存 25% 中较大的一项，锁定内存最多 6 GiB，来宾内存最多 5120 MiB，并核对当前可用内存。

## 安装与启动

从 [Releases](https://github.com/yzddmr6/GunyahVM/releases/latest) 下载软件包：

```sh
sudo apt install ./gunyahvm*_arm64.deb
gunyahvm
```

可在宿主信息页加载驱动。活动本地桌面用户自动获得设备权限。管理员可将远程账户加入 `gunyah` 组，加组后重新登录。详见[安全模型](docs/security.zh-CN.md)。

在界面中选择本地 ARM64 镜像，选择软件显示模式，再创建并启动虚拟机。命令行选项见对应使用指南。

GUI 启动时会访问 GitHub 检查新版本，不发送设备信息；可在应用设置中或通过 `GUNYAHVM_NO_UPDATE_CHECK=1` 关闭。

## 使用与维护

VM 数据位于 `~/.local/share/gunyahvm/`，运行锁位于 `/run/user/UID/gunyahvm/`。修改资源、共享或删除磁盘前先停止 VM。`stop --force` 属于崩溃式断电，可能丢失来宾数据。

每个用户同时运行一个 VM。不提供声音、USB 直通、热迁移、来宾 Secure Boot/vTPM。其他本地账户也能访问 loopback VNC。不导入旧工具或其他工具的 VM 格式。

[Linux](docs/linux.zh-CN.md) · [新增设备](docs/new-device.zh-CN.md)

## 开发与来源

[架构](docs/architecture.zh-CN.md) · [运行组件接口](docs/runtime-components.zh-CN.md) · [构建](docs/building.zh-CN.md) · [贡献](CONTRIBUTING.zh-CN.md)

应用改写复用 **iceland-vms**（mzakamin-hub）的界面、配置、图形、来宾控制和转发代码；旧 GunyahVM 提供普通用户启动、生命周期、镜像和打包。**librevm** 提供国内镜像选择、控制台回放和 balloon 防护思路。运行组件来自 iceland-gvm、DroidVM、crosvm/minijail、virglrenderer、Linux、Mesa、edk2、virtio-win。精确提交和保留声明见 [NOTICE](NOTICE) 与 [debian/copyright](debian/copyright)。

项目采用 GPL-2.0-only；继承组件保留原许可证。分发二进制时一并提供对应源码。[安全问题报告](SECURITY.zh-CN.md)。
