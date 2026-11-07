<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# 更新日志

## 1.0.0 — 首版发布

- 运行 Linux ARM64 来宾，支持云镜像创建、持久化存储、SSH 访问及网络管理。
- 使用用户自备的 ISO、随包提供的 UEFI 和驱动准备并运行 Windows 11 ARM64，通过 Remmina VNC 显示画面。
- 用唯一的 `gunyahvm` ARM64 包安装 Linux、Windows 所需依赖和宿主集成。
- 从实时设备树发现 Gunyah，按宿主内存推算默认资源，为活动本地座席用户或 `gunyah` 组成员提供使用权限。
- 用 `check` 检查宿主，用 `about` 查看作者、许可证和致谢。固件检查优先读取可读设备树；需要回退到原始 FDT 而无法读取时显示 `UNKNOWN`（无法确认）。
- 创建虚拟机或下载镜像前，先检查设备访问权限和 polkit 授权。
- 提供同构中英文用户、设备接入及构建指南，支持使用固定源码和已校验的发布输入构建。
- 由 yzddMr6 创建并维护，源自小米平板 8 Pro 主线移植项目，保留上游署名。
