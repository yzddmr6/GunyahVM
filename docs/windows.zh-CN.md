<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Windows 来宾

## 要求

提供官方 Windows 11 ARM64 ISO 和自己的许可证。Windows 资产默认安装。至少配置 4608 MiB 来宾内存、64 GiB 稀疏磁盘，以及安装介质所需空间和 8 GiB 宿主余量。默认显示为 loopback VNC 上的软件 simplefb，不提供 Windows 3D、vTPM、Secure Boot。驱动使用 DroidVM 公共测试证书和 Windows Test Mode。

## 创建与安装

使用 GTK 创建向导，或：

```sh
gunyahvm create windows --os windows11 --iso ~/Downloads/Windows11_ARM64.iso --edition Professional --start
gunyahvm view windows
```

选择 ISO 中实际存在的版本。WinPE 只给新 VM 磁盘分区，用 DISM 应用所选 WIM，安装随附驱动并复制部署载荷。监督进程须验证匹配的完成 token，才会移除安装介质并从已安装磁盘进入 OOBE。部署失败不会静默重试。

正常 OOBE 创建交互使用的 Windows 账户。无人值守安装可用 `--local-user vmuser --password-fd 3` 从已打开的描述符读取密码；省略 `--password-fd` 时使用终端隐藏输入。密码不放在命令行上。Windows 应答文件编码不是加密，须保护 VM 目录；首次登录会删除应答文件。不启用 Windows 自动登录。

## 来宾控制与显示

应用用每个 VM 独立的密钥建立专用 SSH 控制账户。显式配置本地账户时在安装阶段执行，否则在所有者首次登录后执行，避免控制账户成为唯一可用的 Windows 账户。来宾缺少 OpenSSH Server 时通过 Windows Update 安装，关闭 SSH 密码认证，将 SSH/RDP 防火墙访问限制为宿主网关。安装缺失的 OpenSSH capability 需要来宾内能联网。

概览页报告 agent 就绪与来宾地址。`gunyahvm stop windows` 通过认证后的 SSH 请求关机，等 VMM 退出。密钥被拒、agent 缺失或超时都算失败，不算干净关机。`gunyahvm rdp windows` 打开 RDP 客户端，使用交互 Windows 账户凭据，且 Windows 版本须支持 RDP 服务。`view` 可重连 VNC，供安装或恢复使用。其他宿主本地用户也能访问 loopback VNC。

## 生命周期

Windows 自己请求 reset 时保留同一 VM 锁和网络；`restart` 先等干净关机。`stop --force` 是显式崩溃式断电，可能丢失来宾数据。低电量、过热、宿主睡眠会发起关机；必要时有界睡眠回退会记录强制结束。卸载宿主软件包保留 VM 数据。Windows 随附驱动没有 9p 客户端，因此不向 Windows 提供 Linux 9p 目录共享。
