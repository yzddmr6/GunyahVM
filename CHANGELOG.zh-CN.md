<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# 更新记录

## 1.0.1 — Windows 来宾

- 从自备的 Windows 11 ARM64 ISO 离线部署，通过应用创建本地账户，使用软件显示和本机 VNC。
- OpenSSH 随安装介质离线配置，以密钥 SSH 控制来宾并正常关机；控制账户从 Windows 登录界面隐藏。
- 补齐离线本地账户的初始化配置，正确读取 Windows PowerShell 安装进程退出码。
- 重写双语使用与构建指南，补充 Windows 部署、显示和网络限制。

## 1.0.0 — 首发版

- 通过 GTK4/libadwaita 应用和 CLI 创建本地磁盘镜像的 Linux ARM64 虚拟机，启动与干净关机；关闭管理器再打开可以接回运行中的来宾。
- 软件显示与来宾 GL 加速（virgl）。
- GUI 启动时异步检查新版本，缓存 24 小时，有更新时显示横幅，可通过应用设置或环境变量关闭。
