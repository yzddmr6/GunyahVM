<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# GunyahVM

**把运行主线 Ubuntu 的骁龙平板变成随身虚拟机工作台：用 Gunyah 硬件虚拟化运行 ARM64 Linux、Windows，以及本地组装的 macOS，在同一个应用里打开桌面、切换实例和正常关机。**

[English](README.en.md) · [下载](https://github.com/yzddmr6/GunyahVM/releases/latest) · [问题反馈](https://github.com/yzddmr6/GunyahVM/issues)

![GunyahVM中的Ubuntu桌面](docs/images/manager.zh-CN.png)

## 能做什么

- **平板上的多系统桌面**：Ubuntu自动下载和配置；Windows从自己的ARM64 ISO离线安装；macOS可导入自己本地组装的原生APFS启动包。
- **应用内显示**：来宾桌面直接显示在GTK窗口中，可缩放、全屏并重新打开。VM和普通桌面应用分开运行，关闭管理窗口不会自动关机。
- **按来宾区分的图形能力**：Linux有virgl OpenGL加速；macOS本地配套来宾可使用Turnip/Metal图形路径；Windows目前为软件帧缓冲，不提供3D加速。
- **日常管理**：GUI和CLI共用配置，支持NAT网络、SSH控制、端口转发和停机磁盘快照。Linux支持共享文件夹和按需文本剪贴板；macOS文本剪贴板需已登录桌面。

| 来宾 | 创建方式 | 显示与集成 |
| --- | --- | --- |
| Ubuntu / Linux | 自动下载Ubuntu，或导入已安装的ARM64 raw/qcow2磁盘 | 应用内显示、virgl、文本剪贴板、virtiofs/9p共享 |
| Windows | 自备Windows 11 ARM64 ISO，应用离线部署 | 固定分辨率软件显示、密钥SSH；无共享目录、文本剪贴板或3D加速 |
| macOS | 导入自己组装的native APFS bundle；从零自动创建尚未提供 | 固定1280×800、配套来宾GPU、文本剪贴板；无共享目录 |

macOS图形负载已在小米平板8 Pro上验证过指定组合；一加上的GPU可靠性仍有已知问题。不要把一台设备的结果当成所有设备均可用。

## 怎么工作

**管理界面与核心。** GTK/libadwaita应用和命令行调用同一个核心，读取同一份用户库。设置、状态、正常关机与快照有一致的行为。

**虚拟机与显示。** crosvm通过内核Gunyah接口运行ARM64来宾；Casilda接收本机Wayland显示。Linux的virgl和macOS的专用图形路径各有对应运行组件，并非Windows也能自动获得GPU加速。

**宿主集成与数据。** 小型特权助手负责权限和网络，VM以普通用户运行。应用/运行时更新与用户磁盘分开，系统镜像及密钥留在本机。详细边界见[架构](docs/architecture.zh-CN.md)与[安全模型](docs/security.zh-CN.md)。

## 下载与安装

需要ARM64主线Ubuntu、支持Gunyah的设备内核、Wayland桌面及包声明的GTK/Casilda依赖。推荐使用设备项目的最新系统：[小米平板8 Pro](https://github.com/yzddmr6/xiaomipad-8pro-mainline/releases/latest)。安装应用不能给不支持Gunyah的内核补上硬件能力。

1. 从同一次[发布](https://github.com/yzddmr6/GunyahVM/releases/latest)下载全部四个`gunyahvm*_arm64.deb`，放入一个只含该次发布包的目录。Casilda等额外依赖按发布页说明安装。
2. 在该目录执行：

   ```sh
   sudo apt install ./gunyahvm*_arm64.deb
   ```

3. 用普通桌面账户打开GunyahVM。主菜单→**系统检查**查看驱动、权限、内存与运行时；**加载驱动**仅加载当前内核已有模块，不替换内核。

## 创建和使用

### Ubuntu / Linux

点击 **+ → Ubuntu 26.04 LTS → 创建**；需要桌面时选择图形模式，完成后点击**启动**、**打开显示**。也可选**Linux磁盘镜像**导入已安装的ARM64 raw/qcow2系统，不能选安装ISO；自动账户配置需要cloud-init。

```sh
gunyahvm load
gunyahvm create ubuntu --mirror official --guest-payload --gpu virgl --start
gunyahvm list
gunyahvm ssh ubuntu
gunyahvm stop ubuntu
```

停机后在设置中添加只读/可写共享目录，来宾访问`/mnt/gunyahvm/<标签>`。文本剪贴板需要来宾Wayland桌面安装`wl-clipboard`，从VM菜单按需收发。更多镜像选项见[Linux指南](docs/linux.zh-CN.md)。

### Windows

准备有效的Windows 11 **ARM64** ISO和许可证，本项目不提供Windows镜像或激活。点击 **+ → Windows 11**，选ISO、填写本地账户和密码、确认资源并创建。启动后自动部署；打开显示完成地区、键盘、许可和隐私选项。

```sh
gunyahvm create windows --os windows11 --iso /path/to/windows-arm64.iso --local-user vmuser
gunyahvm start windows
gunyahvm view windows
```

密码在终端隐藏输入；自动化用`--password-fd`，不要写进命令行。至少4608 MiB内存、64 GiB逻辑磁盘，实际磁盘按需增长。安装不需联网，默认NAT仍允许来宾访问外网。正常关机需要来宾网卡和SSH控制服务。详见[Windows指南](docs/windows.zh-CN.md)。

### macOS：许可、官方输入与导入

**Apple的软件许可只允许按其条款在Apple品牌硬件上虚拟化macOS。高通平板不属于Apple硬件，用户需阅读许可并自行决定；本项目不授予额外权利。** [Apple软件许可](https://www.apple.com/legal/sla/)。本项目不提供、不分发Apple软件或预装系统盘。

当前可用入口是导入本地组装的启动包，**没有可直接执行的从零自动创建命令**。不要把下面的输入流程当成已完成的一键安装：

1. 从Apple官方渠道取得对应系统的IPSW及构建所需SDK，阅读对应许可；不要下载第三方预装盘、解密后的内核或共享控制密钥。
   获取固定组合的两份官方IPSW（共约46.5GB，另需SDK和展开空间）：

   ```sh
   gunyahvm fetch-macos-inputs "$HOME/.cache/gunyahvm/macos-official"
   ```

   Ctrl-C取消；重跑同一命令续传。下载只跟随Apple域名的HTTPS跳转，完整SHA256通过后才作为有效输入。失败后可用下列命令只清除未完成文件，已验证文件保留：

   ```sh
   gunyahvm fetch-macos-inputs "$HOME/.cache/gunyahvm/macos-official" --clean-partials
   ```

2. 在本机使用匹配的构建工具组装原生APFS System/Data卷与启动包，并给新VM生成控制密钥、把公钥写入该来宾。当前社区组装过程还需要额外工具链准备，本项目尚未把它做成完整创建器。
3. 已有匹配bundle时，点击 **+ → macOS → 选择本地启动包 → 导入**，然后启动。包内的`inputs.json`校验启动文件、磁盘和控制密钥；固件需留在本机VM目录或来自升级保留的本地文件。

也可用CLI导入和启动：

```sh
gunyahvm import-macos macos /path/to/local-bundle
gunyahvm start macos
gunyahvm view macos
```

可移植bundle使用schema 3，把`firmware.fd`纳入哈希清单；旧schema 2继续读取升级保留的本地固件。

分辨率固定；更改内存或CPU需重新组装匹配的启动输入。克隆因需要来宾侧换钥而暂不可用。原型还存在fskitd限制及故障恢复缺口，详见[macOS要求与限制](docs/macos.zh-CN.md)。

## 升级与保留数据

正常关闭所有VM，下载同次发布的四个包，再运行上述`apt install`。**不用先卸载，不要删除或重建已有VM。** 数据继续位于`~/.local/share/gunyahvm/vms/`；备份时先停机，连同配置和密钥一起备份。

从原私有macOS包升级时，安装脚本在旧包文件移除前将已有固件保留到`/var/lib/gunyahvm/local-macos/firmware.fd`，不改用户库。若该处已有不同固件会停止升级，避免覆盖。已有macOS、Ubuntu、Windows的磁盘和控制身份继续保留。版本通知只提供下载入口，不自动安装。

## 常见问题

- **无法启动？** 打开系统检查，或执行`gunyahvm check --json`。确认内核支持、Wayland会话、内存和依赖；不要用root运行GUI。
- **窗口大小变了，来宾分辨率没变？** 当前窗口适配是缩放。Windows/macOS启动几何固定，动态分辨率没有实现。
- **GUI关闭后VM还在？** 这是正常行为。重新打开并选择显示；需要停止时点**关机**。强制停止相当于断电，可能丢数据。
- **共享/剪贴板不可用？** 先看上面的来宾能力表。Windows目前未提供；macOS没有共享目录；Linux剪贴板需已登录Wayland会话和来宾工具。
- **能同时跑几台？** 当前每用户一台。不提供声音、USB直通、热迁移、来宾Secure Boot或vTPM。
- **怎样报错？** 提交设备、系统/应用版本、错误和复现步骤到[Issues](https://github.com/yzddmr6/GunyahVM/issues)。删除日志里的个人信息和来宾数据，勿上传Apple文件或密钥；漏洞用[安全报告](SECURITY.zh-CN.md)。

可写共享目录可被来宾修改或删除；CLI显式开启的旧式VNC可被其他本机账户访问。NAT不等于外网隔离。

## 开发与致谢

[构建](docs/building.zh-CN.md) · [贡献](CONTRIBUTING.zh-CN.md) · [新增设备](docs/new-device.zh-CN.md)。感谢iceland-vms/iceland-gvm、DroidVM、crosvm、minijail、Casilda、virglrenderer、Linux/Mesa、GTK/libadwaita、librevm、edk2及virtio-win，准确来源见[NOTICE](NOTICE)。

GunyahVM采用[GPL-2.0-only](LICENSE)，第三方组件保留各自[许可证](debian/copyright)。
