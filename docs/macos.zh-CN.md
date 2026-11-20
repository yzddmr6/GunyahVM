<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# macOS

GunyahVM 在平板上直接创建 macOS 27.2 虚拟机：应用从 Apple 的服务器只下载需要的部分，在本机装好，第一次启动就是登录界面。macOS 以 ARM64 原生运行在 Gunyah 上，图形经 Metal 插件走 Adreno GPU。

GunyahVM 不附带 Apple 软件，macOS 的文件都由你的设备直接从 Apple 下载。使用 macOS 需遵守 Apple 的[软件许可协议](https://www.apple.com/legal/sla/)。

## 新建

**图形界面**：点击 **+ → macOS**，填写虚拟机名称、macOS 账户名和密码，选择磁盘大小，点击**创建**。进度条显示下载和安装进度；随时可以点**暂停**，之后在同一项上点**继续**，从停下的地方接着做。点**丢弃**删除未完成的创建（已下载的文件保留，下次不用重下）。

**命令行**：

```sh
gunyahvm create-macos macos            # 询问 macOS 账户密码；默认账户名是你的用户名
gunyahvm start macos
gunyahvm view macos
```

- 中断后（Ctrl-C、断网、关机）重跑同一条命令即可续传、续装。
- `--account NAME` 指定账户名，`--password-fd N` 从文件描述符读密码（脚本用，不要把密码写进命令行），`--disk-gib N` 指定磁盘大小（40–512，默认 64）。
- `--keep-downloads` 保留 Apple 的文件，再建一台 macOS 时不用重新下载；`--discard` 删除这台未完成的创建。
- `gunyahvm fetch-macos-inputs` 只下载、不安装，可以先在网络好的地方下好。

**需要的资源**：下载约 13.3 GB；创建过程中最多约 27 GB 可用空间，完成后虚拟机约占 22 GB（磁盘按需增长）。虚拟机使用 9 GB 内存和 6 个处理器；开机时，除去正在运行的虚拟机要用的内存，还要有这么多可用内存。创建期间可以照常使用平板和别的虚拟机；最后写入磁盘的几分钟里，写盘用的辅助虚拟机需要 2 GB 可用内存。

## 创建时发生了什么

1. **下载**：从 Apple 的 CDN 按成员下载两个 macOS 27.2 恢复映像中用到的 7 个文件（系统卷、两个 cryptex、内核集合和三个信任缓存），每个都核对 ZIP 校验和与固定的 SHA-256。HTTP 断点续传，只跟随 Apple 域名的 HTTPS 跳转。
2. **解密**：系统映像是 Apple 加密归档（AEA），密钥从 Apple 公开的密钥服务器取得，逐段校验后解开，直接写进新磁盘。
3. **链接内核**：把 GunyahVM 的 XNU 内核和板级驱动链接进 Apple 的内核集合，结果与发布前验证过的版本逐字节一致。信任缓存由 Apple 的三组加上 Metal 插件组成。
4. **写入磁盘**：建一个 APFS 容器，含 System、Data、Preboot、VM、Update、Hardware、xART 卷。文件写入由一台临时的 Linux 助手虚拟机完成（打包的来宾内核和 APFS 驱动），所以宿主内核不需要 APFS 支持，也不需要管理员密码；助手结束后自动关机。
5. **账户和驱动**：写入你的管理员账户（只保存加盐哈希，暂停期间也不保存密码明文）、跳过首次设置向导、装 Metal 插件，并为这台虚拟机生成新的控制密钥，公钥写进来宾。每台虚拟机的密钥都不同，只存在本机。
6. **启动盘**：生成这块板子的 Apple 设备树和 EFI 启动盘。
7. **发布**：全部完成后，虚拟机一次性出现在库里；未完成的创建只在 `~/.local/share/gunyahvm/.creating/` 下，不会出现半成品。

## 使用

启动后在应用里打开显示，用创建时的账户登录。正常关机用 **关机**（经控制通道请求来宾关机），不要强制停止。文本剪贴板在登录桌面后可用，从虚拟机菜单收发。

## 规格与限制

| 项目 | 现状 |
| --- | --- |
| 内存 / 处理器 | 固定 9 GB / 6 个 |
| 分辨率 | 固定 1280×800；窗口适配是缩放 |
| 磁盘 | 40–512 GB，创建时选定 |
| 图形 | Metal（Adreno，经 Turnip） |
| 网络 | NAT |
| 剪贴板 | 文本，登录后可用 |
| 共享文件夹、声音、USB | 没有 |
| Apple ID、iCloud、App Store 等依赖 Apple 硬件身份的服务 | 不可用 |
| 克隆 | 不可用（克隆需要来宾侧换密钥） |

支持的设备：小米平板 8 Pro（系统 1.3.1 起）、一加平板 2 Pro（v0.9.1 起）。一加平板 2 Pro 上 macOS 在较重的图形负载下可能卡住，需要强制停止后重新启动虚拟机。

GunyahVM 按运行中内核的能力判断能否启动 macOS，不看版本号：需要 Gunyah 的 VM-fd 内存共享，图形还需要可访问的 Adreno 渲染节点和 `/dev/udmabuf`，所以自己编译的内核也能运行 macOS。macOS 跑重图形负载时，宿主内核还需要带两项 msm 驱动修复，应用探测不到它们：“drm/msm: allow virtually contiguous submit bookkeeping”（上游 Linux 有等价修改）和“drm/msm: honor write-back system RAM on coherent GPU imports”。

## 启动套件

安装包里的 `/usr/lib/gunyahvm/macos/kit/` 是 GunyahVM 自己的部分，`kit.json` 列出每个文件的哈希：

- XNU 内核：Apple 开源的 xnu-12377.121.6 加 Droid-VM/macOS-DroidVM 的补丁（Gunyah 板级适配、内核日志走串口），以及 GunyahVM 的大内存布局（4 KiB 页表）。
- 板级驱动：AES、PCIe MSI、APFS 卷可见性、平台替身、显示（virtio-gpu）、输入、GPU（Adreno）等 7 个 kext 的目标文件，在设备上链接。磁盘和网卡是 PCIe 上的 virtio 设备，由 macOS 自带的驱动以 MSI-X 中断驱动。
- Metal 插件：Turnip（Mesa）加 metal2vulkan 桥，以及 Shader Cache 应用。
- UniXNU 与 DarwinBoot（EFI 启动器）、EDK2 固件、这块板子的设备树。
- 助手虚拟机用的 linux-apfs-rw 模块、GNU tar，以及 mkapfs（apfsprogs）。

内核、kext、插件和启动器由 GunyahVM 用 macOS-DroidVM 自己的构建环境从公开源码编译；来源与构建方式见 [NOTICE](../NOTICE) 和 [构建](building.zh-CN.md)。

## 已有实例

GunyahVM 1.5 及更早版本建的 macOS 实例，第一次启动时会自动更新：磁盘原地改成 GPT 磁盘，不复制你的数据；实例里的 Metal 插件和启动盘换成这个版本的。需要几分钟，并从 Apple 下载一次约 23 MB。从私有 macOS 包或导入的启动包升级时，实例的密钥和配置原样保留。固件优先用虚拟机目录里的 `firmware.fd`，其次是启动套件里的固件，再次是旧包升级时保留在 `/var/lib/gunyahvm/local-macos/firmware.fd` 的副本。以前组装的启动包仍可在 **+ → macOS → 高级** 里导入，或用 `gunyahvm import-macos NAME BUNDLE`。

## 故障排除

- **下载很慢或中断**：暂停后换个网络继续，已下载的部分不会重下。
- **空间不足**：创建前会检查可用空间；腾出空间后点**继续**。
- **“Stop your running virtual machine before creating macOS”**：先关掉正在运行的虚拟机。
- **助手虚拟机失败**：错误信息里有助手的最后几行日志，完整日志在 `~/.local/share/gunyahvm/.creating/NAME/helper-*.log`。可以直接**继续**重试；仍失败就**丢弃**后重新创建。
- **忘了 macOS 密码**：目前只能删除后重建。
