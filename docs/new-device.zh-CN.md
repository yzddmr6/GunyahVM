<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# 新增设备档案

本文说明何时需要新增设备档案，以及如何描述传感器和显示默认值，避免把档案误当成内核支持。

## 先检查宿主

设备须已有匹配固件和实现 Gunyah 宿主 ABI 的内核。运行 `gunyahvm load`，再用 `gunyahvm check --json` 检查当前宿主。档案不能添加驱动、改变固件资源 ID，也不能让不支持的图形功能生效。

档案显示 `UNKNOWN` 表示没有匹配到设备树，与能力检查失败是两回事。未知设备也按宿主可用资源设置默认值。设备需要传感器选择或显示默认值时，再添加档案。

## 描述设备

参照已提供的档案创建 `data/devices/<id>.toml`。Schema 1 使用 `id`、`name`、精确的 DT `compatible` 字符串、`sensors`、`display` 和解释用途的 `reasons`。匹配遵循设备树从具体到通用的顺序，重复的 compatible 匹配会被拒绝。

Compatible 字符串和电池名称应来自目标设备自己的 DTS 与文档。电池列表为空时启用运行时发现；非空时只读取列出的电源名称。温度保护自动发现所有带 passive 温控点的热区，档案不设置固定温度或手工热区列表。板级温控例外必须先有明确、可核对的硬件依据，不能仅以通用策略数字覆盖内核温控点。电量读数只用于显示，不会阻止或停止虚拟机；读不到温控点的热区会被跳过。

档案不能包含 VMID、capability ID、命令、按内核版本切换的逻辑或功能开关。资源管理器 ID 和中断来自 live DT/FDT，图形可用性来自宿主与运行组件的实际能力。二进制组件变更遵循[运行组件接口](runtime-components.zh-CN.md)。

## 检查集成

添加档案后运行离线检查：

```sh
python3 tools/lint.py
python3 -m pytest -q tools/tests/test_capabilities.py tools/tests/test_host.py
```

在目标系统上确认 `gunyahvm check --json` 选中了正确档案，且能读取有效传感器数据。再用打包后的应用检查虚拟机创建、启动、来宾操作、正常关机和资源回收。如果要说明图形支持，还需检查实际显示。档案匹配或探测成功本身不能证明虚拟机可用。

确认固件、内核和应用这一组合可用后，再记录设备所需系统版本和用户可用功能。设备身份与策略应留在档案中，不在图形界面或虚拟机核心中增加按设备判断的分支。
