<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# macOS

Apple 的 macOS 软件许可只允许按其条款在 Apple 品牌硬件上虚拟化；高通平板不是 Apple 品牌硬件。用户需阅读对应版本许可并自行决定，本项目不授予额外使用权。见 [Apple 软件许可](https://www.apple.com/legal/sla/)和 [macOS 27 许可](https://www.apple.com.cn/legal/sla/docs/macOS27.pdf)。

GunyahVM 不提供、不分发 Apple 软件。IPSW、内核集合、提取组件、SDK、启动镜像和虚拟机磁盘均为本地用户输入。macOS 运行时包只带宿主侧 crosvm、libvncserver、virglrenderer；不带固件或来宾图形二进制。

GunyahVM能通过“新建虚拟机 → macOS”导入已经组装好的原生 APFS bundle，**尚未提供官方下载到首次启动的完整创建器**。上游构建脚本还依赖额外的 SDK/工具链准备，导入磁盘不能代替安装。

## 已有实例升级

先正常关闭全部 VM，再一起升级应用及匹配运行时包。不要卸载旧版、重建实例或删除目录。运行时 preinst 会在 dpkg 删除旧包文件前，把已经安装的 macOS 固件保留到本机 `/var/lib/gunyahvm/local-macos/firmware.fd`，不遍历、不改写用户库。若该处已有不同固件，升级停止而不覆盖。

macOS、Ubuntu、Windows 配置继续放在 `~/.local/share/gunyahvm/vms/`，ID、磁盘和原控制身份保留。macOS 优先读取 VM 目录内的 `firmware.fd`，其次读取上述保留文件。GUI 重连沿用原 VMM；设备兼容性仍取决于匹配的宿主和来宾组件。

## 已知限制

- 启动几何在组装时固定，目前1280×800。适应窗口是缩放，不是动态分辨率；更改内存或CPU需重新组装匹配启动输入。
- 8 Pro已通过组合为9 GiB/6核；一加GPU可靠性仍在排查，不新增稳定性承诺。
- 文本剪贴板需要已登录桌面和认证控制通道；此配置没有共享目录。不承诺原生多点、动态分辨率及其他设备功能。
- 原型输入仍停用fskitd；反复启动与GPU/pshare故障回收仍是验收项。
- 组装辅助函数可在本机生成独立控制密钥，但把公钥写入新APFS来宾仍属于未完成的创建器。导入拒绝与目标库已有VM共用密钥；导入到另一个库本身不会轮换来宾密钥。macOS克隆在实现来宾侧换钥前禁用，已有实例不自动换钥。

正式创建器必须只从Apple取输入，支持续传、取消后保留已验证内容、仅清理自己的暂存目录，为每个新VM生成并安装独立密钥，组装成功后才发布实例。这些是发布门槛，不是当前已完成能力。
