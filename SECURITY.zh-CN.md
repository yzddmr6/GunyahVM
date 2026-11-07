<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# 安全策略

## 报告

向 yzddMr6 的 **yzddmr6@gmail.com** 发送漏洞报告，提供受影响版本、威胁模型、最小复现和预期影响。协调披露之前，不要公开带利用细节的 issue；不要发送密码、私钥、ISO 内容或完整虚拟机磁盘。维护者会与报告者讨论修复和披露时间，不承诺响应时限。

## 范围

当前 1.0 系列会在可用时接收安全修复。报告范围包括管理命令、权限助手、打包和自带补丁。crosvm、minijail、edk2 和驱动问题可能需要与上游协调。请阅读[安全模型](docs/security.zh-CN.md)，特别是本地 UID 共享、每进程有限内存额度、Windows 测试签名驱动及本机 VNC 访问。
