<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# GunyahVM

**在 Qualcomm Gunyah 上运行 Linux 与 Windows 虚拟机，提供 GTK 桌面应用和共用核心的命令行。** 由 yzddMr6 创建并维护，与 Qualcomm 无关联。

[English](README.md) · [Linux 指南](docs/linux.zh-CN.md) · [Windows 指南](docs/windows.zh-CN.md) · [下载](https://github.com/yzddmr6/GunyahVM/releases/latest)

## 路线选择

所有设备共用一个应用。硬件能力由运行时探测，按 DT 匹配的小型设备档案只保存资源上限和守护策略。crosvm、渲染器和监督进程都以普通账户运行，发行版集成与虚拟机核心分离。

## 功能

| 功能 | 提供内容 |
| --- | --- |
| GTK4/libadwaita 界面与 CLI | 列表、创建、设置、控制台和宿主信息 |
| Linux ARM64 | 云镜像或本地磁盘、SSH、cloud-init、干净关机 |
| Linux 图形 | 软件显示；只有宿主、运行组件和来宾具备对应能力时才显示 virgl、Venus、native 模式 |
| Windows 11 ARM64 | 用户提供 ISO，WinPE/DISM 部署、loopback VNC 软件显示、SSH 密钥控制，以及支持该功能的版本中的 RDP |
| 共享与网络 | 可写的 Linux 9p 共享、NetworkManager NAT、可选 vhost-net、本机 TCP/UDP 端口转发 |
| 生命周期与守护 | 重启、有界控制台回放、低电量/温度守护、宿主睡眠前关机 |

默认包含 Windows 固件、驱动和准备工具，不提供 Windows ISO、许可证、激活密钥或已安装的来宾系统。关闭管理器窗口不会停止虚拟机。

## 设备与要求

| 设备档案 | DT compatible |
| --- | --- |
| 小米平板 8 Pro | `xiaomi,piano` |
| 一加平板 2 Pro | `oneplus,erhai` |

未知设备使用保守默认值，并报告 `UNKNOWN`；档案不会开启硬件能力。固件须提供 Gunyah RM 信息，宿主内核须实现 Gunyah VM ABI。deb 面向提供 GTK4/libadwaita、systemd/logind、udev、polkit 和 NetworkManager 的 Ubuntu ARM64。安装应用不会补上缺失的内核支持。

启动时保留 1 GiB 与宿主总内存 25% 中较大的一项，锁定内存最多 6 GiB，来宾内存最多 5120 MiB，并核对当前可用内存。Windows 至少需要 4608 MiB 来宾内存及 64 GiB 稀疏磁盘；安装介质准备还会保留至少 8 GiB 空闲磁盘。

## 安装与启动

从 [Releases](https://github.com/yzddmr6/GunyahVM/releases/latest) 下载软件包：

```sh
sudo apt install ./gunyahvm*_arm64.deb
gunyahvm
```

可在宿主信息页加载驱动。安装的 logind 延迟配置在 logind 下次启动时生效；首次安装后重启宿主。活动本地桌面用户自动获得设备权限。管理员可将远程账户加入 `gunyah` 组，加组后重新登录。详见[安全模型](docs/security.zh-CN.md)。

```sh
gunyahvm check
gunyahvm load
gunyahvm create ubuntu --start
gunyahvm list
gunyahvm ssh ubuntu
gunyahvm stop ubuntu
```

没有指定 SSH 密钥时，应用会生成密钥。`check --json` 导出宿主能力；`check` 区分失败项与无法确认的固件/档案信息，不加载驱动、不提升额度。当前 shell 的低 memlock 额度可由授权启动器针对新 VM 提供。

## 使用与维护

VM 数据位于 `~/.local/share/gunyahvm/`，运行锁位于 `/run/user/UID/gunyahvm/`。修改资源、共享或删除磁盘前先停止 VM。`stop --force` 属于崩溃式断电，可能丢失来宾数据。睡眠守护先尝试干净关机；超时或强制结束记录为失败，不会冒充干净关机。

每个用户同时运行一个 VM。不提供声音、USB 直通、热迁移、Windows 3D、来宾 Secure Boot/vTPM。Windows 使用测试签名驱动和 Test Mode。其他本地账户也能访问 loopback VNC。不导入旧工具或其他工具的 VM 格式。

[Linux](docs/linux.zh-CN.md) · [Windows](docs/windows.zh-CN.md) · [新增设备](docs/new-device.zh-CN.md)

## 开发与来源

[架构](docs/architecture.zh-CN.md) · [运行组件接口](docs/runtime-components.zh-CN.md) · [构建](docs/building.zh-CN.md) · [贡献](CONTRIBUTING.zh-CN.md)

应用改写复用 **iceland-vms**（mzakamin-hub）的界面、配置、图形、来宾控制和转发代码；旧 GunyahVM 提供普通用户启动、生命周期、镜像、Windows 部署和打包。**librevm** 提供国内镜像选择、控制台回放和 balloon 防护思路。运行组件来自 iceland-gvm、DroidVM、crosvm/minijail、virglrenderer、Linux、Mesa、edk2、virtio-win。精确提交和保留声明见 [NOTICE](NOTICE) 与 [debian/copyright](debian/copyright)。

项目采用 GPL-2.0-only；继承组件保留原许可证。分发二进制时一并提供对应源码。[安全问题报告](SECURITY.zh-CN.md)。
