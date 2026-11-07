<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# 新设备接入

## 确认平台

从固件提供 Gunyah、内核实现 README 所列 ABI 的高通设备开始，保留独立恢复方式。执行 `gunyahvm check`，报告经审阅的输出、根 compatible、SoC、内核版本/配置和固件版本。不要公开序列号、账户名、私人路径或完整原始固件转储。

检查实时 `/sys/firmware/devicetree/base` 或 `/sys/firmware/fdt`，不要复制其他设备的 VMID、能力 ID 或保留地址。固件可能不公布宿主 VMID；新来宾 VMID 由资源管理器分配。命令验证 hypervisor/resource-manager 节点和其中两个 64 位能力 ID。protected VM 能力和正确的宿主固件仍是前提。

## 设备差异

先用默认值。已列出的两台设备不需要随包覆盖配置。只有确实证明必要的资源上限才放入 `/usr/share/gunyahvm/devices/<compatible>.conf`，归 root 所有且其他人不可写：

```ini
[gunyahvm]
max_memory_mib = 3072
max_cpus = 2
```

选择第一个匹配的根 compatible 文件。配置只在启动时降低上限，不能设置 VMID、能力 ID、命令、路径、固件或其他虚拟化层，这些不属于设备配置。未知键或错误归属直接拒绝。内核 ABI 变化应在内核解决，或做成注明来源及上游状态的独立 crosvm 补丁。

## 验证与报告

先验证 `check`、模块加载、本地座席自动权限以及 VMM 有限额度。使用另一个 UID 的未加组 SSH 登录作拒绝负例，再确认加组用户可用。切换座席后验证新打开设备的权限被撤销。随后验证 Linux 启动/就绪/SSH/NAT、停止/重启、内核刷新及强停记录。Windows 验证正常部署交接、OOBE、VNC 重连、重启和关机。挂起或宿主重启前停止来宾；重复启动不得残留 TAP 或 crosvm 子进程。按新设备模板提交证据和限制，不能仅凭离线构建成功标成已验证。
