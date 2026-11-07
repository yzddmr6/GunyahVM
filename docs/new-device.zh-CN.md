<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# 新增设备档案

## 从能力开始

设备须提供 Gunyah 宿主 ABI 和匹配固件。运行 `gunyahvm check --json` 查看运行中的宿主能力。未知档案不同于能力检查失败，应用仍采用保守默认值。不要把设备支持表或内核版本猜测写进前端/核心代码。

## 档案数据

按照随附档案增加 `data/devices/<id>.toml`。schema 1 包含 `id`、`name`、精确 DT `compatible` 字符串、`resources`、`guards`、`sensors`、`display` 和说明理由的 `reasons`。按 DT 的具体程度顺序匹配，拒绝重复匹配。

只使用目标设备自己的 DTS 和文档事实。资源上限和应用守护阈值若不是硬件额定值，必须注明属于策略。空传感器名称列表表示运行时发现；非空列表只读取指定电源名称或温区类型。已知平板档案在启动前要求电池和温度读数可用。

档案不提供 VMID、capability ID、可执行命令、内核版本开关或功能标志。RM ID/IRQ 来自 live DT/FDT；图形来自真实宿主/运行组件能力。运行载荷变更遵守[组件接口](runtime-components.zh-CN.md)。
