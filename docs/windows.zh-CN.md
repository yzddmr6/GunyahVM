<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# Windows 用户指南

## 输入

Windows 能力包含在 `gunyahvm` 中；Remmina、VNC 插件、wimtools、7zip、ntfs-3g、python3-hivex、dosfstools 和 mtools 都是强制依赖。自行提供包含 `sources/install.wim` 的官方 Windows 11 ARM64 ISO 和适用许可证。不分发 Microsoft 介质或预置账户。在 ext4 或 XFS 上预留 40 GiB 空间，用于准备介质及不断增长的 64 GiB 稀疏磁盘。

## 安装与显示

```sh
gunyahvm load
gunyahvm create windows --os windows11 --iso ~/Downloads/Windows11_ARM64.iso --start
gunyahvm view windows
```

从 WIM 列表选择版本，或用 `--edition 'Windows 11 Pro'` 指定准确名称。默认最多四个在线 vCPU、4608 MiB 内存和 256 MiB DMA 池。来宾上限不超过 5120 MiB，小内存宿主的上限会降低；达不到最低预算时拒绝创建。

准备介质不提权、不挂载。WinPE 使用 DISM 部署 Windows 并安装附带驱动。成功部署且正常关机后，工具卸下并删除安装介质，然后从系统盘进入普通 OOBE。失败时保留介质和日志，强制停止不会标记安装成功。在 Remmina 内完成 OOBE；Windows 请求重启导致断连时，再执行 `view`。

VNC 只监听 `127.0.0.1`，`--vnc-port 5901` 可选择其他非特权端口。VNC 没有密码，其他本地账户也能连接，不要向网络暴露端口。1280×720 帧缓冲使用软件渲染。

## 账户与关机

默认不创建本地账户、不自动登录、不开 RDP。需要显式创建账户时使用 `--local-user vmuser` 并交互输入密码。Windows 应答文件的隐藏密码编码不是加密。准备文件仅所有者可读，部署后删除宿主安装介质，第一次登录后删除来宾应答文件。需要 RDP 时在支持的 Windows 版本中自行开启，并核对服务器证书。

从 Windows 开始菜单关机，再执行：

```sh
gunyahvm stop windows
gunyahvm start windows
gunyahvm view windows
```

Windows 正在运行时，正常 `stop` 要求先在来宾内部关机。`stop --force` 为硬停止，可能丢数据。保持宿主唤醒，宿主关机前先关来宾。停止后 `delete windows` 永久删除磁盘和日志。卸载包保留虚拟机数据。

## 限制

没有 vTPM、来宾 Secure Boot、GPU 加速、音频和 USB 直通。绕过 TPM 等安装检查；测试签名驱动需要 Test Mode 并显示水印。休眠和快速启动已关闭。Gunyah 的 protected 执行模式不意味着来宾安装和驱动经过安全启动验证。参见组件许可证和[安全模型](security.zh-CN.md)。
