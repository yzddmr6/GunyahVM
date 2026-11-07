<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# 更新记录

## 1.0.0 — 首发版

- GTK4/libadwaita 虚拟机管理器与 CLI 共用校验后的核心和普通用户监督进程；关闭应用不停止来宾。
- 探测固件、Gunyah 扩展、GPU 权限及运行组件；按 DT 匹配的设备档案只提供上限、传感器来源和守护策略。
- 仅在宿主/运行组件/来宾满足要求时提供对应 Linux 图形模式；支持可写 9p 目录、可选 vhost-net 及本机端口转发。
- Linux 云镜像使用官方校验和，可选国内镜像；提供 cloud-init、来宾 agent 和有界控制台回放。
- 从用户提供的 ARM64 ISO 部署 Windows，随附 UEFI/驱动，提供 VNC、密钥来宾控制、RDP，并区分干净关机与强停。
- 使用唯一固定动作 polkit 助手，降权前设置有限 memlock；网络由 UID 派生，睡眠使用 logind delay inhibitor。
- 提供 ARM64 应用及精确匹配的 crosvm/virglrenderer、来宾内核、来宾 Mesa 包，运行组件清单契约、保留的来源声明、离线回归测试及中英文指南。
