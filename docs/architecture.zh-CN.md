<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# 架构

## 组件

`bin/gunyahvm` 导入 `gunyahvm` Python 包。`cli.py` 负责 create/start/stop/list/plan/console/ssh/view/delete 和监督；`image.py` 准备 Linux 磁盘与启动文件；`windows_vm.py` 和 `windows/` 准备并监督 Windows。唯一虚拟化后端是 Gunyah。crosvm 的内部上游代码保留，产品不提供其他后端的抽象。

```mermaid
flowchart LR
    CLI[gunyahvm 用户命令] --> PK[polkit 授权]
    PK --> LOAD[固定模块加载器]
    PK --> NET[所属 NetworkManager TAP]
    PK --> RUN[有限 memlock 然后降权]
    RUN --> SUP[普通用户监督进程]
    SUP --> VMM[crosvm 与 minijail]
    VMM --> DEV[/dev/gunyah]
    DEV --> RM[Gunyah 资源管理器]
```

## 发现与资源

`host.py` 读取实时设备树，包括 compatible、已发布的 VMID 和 64 位资源管理器能力 ID。发行版隐藏 `/hypervisor` 的 sysfs 目录时，回退到内核提供的实时 FDT。缺失的 VMID 如实报告，不用设备常量补齐。新来宾 VMID 由资源管理器通过内核分配，不从机型表挑选。实际 hypercall 使用的权威 ID 由内核解析并通过 ioctl 返回给 crosvm。

内存默认值取自 MemTotal，为宿主至少保留 1 GiB 或 25%，保留已测试的 6 GiB memlock / 5120 MiB 来宾上限，启动时检查 MemAvailable。可选的 root 所有文件 `/usr/share/gunyahvm/devices/<compatible>.conf` 只允许降低资源上限。不附带设备覆盖配置。

## 进程与数据生命周期

特权启动器设置有限 RLIMIT_MEMLOCK，初始化附加组，丢弃全部 real/effective/saved GID 和 UID，然后执行固定的隔离 Python 入口。root 阶段不读用户虚拟机配置。监督进程和 crosvm 使用不同进程组，crosvm 的清理不能杀死监督进程。状态记录 boot ID 和进程启动 ticks，避免 PID 重用误判。每个用户的运行锁限制一台虚拟机，其他用户属于独立信任边界。

虚拟机数据由所有者私有。TAP 名和 NetworkManager 连接 UUID 从虚拟机 UUID 与调用者身份派生。来宾控制消息报告就绪并确认关机；crosvm 扩展退出码区分正常停止、重启及故障。Windows 安装完成必须同时满足部署 token 匹配和正常关机。

## 固定组件

`tools/crosvm.lock.json` 固定上游、minijail、工具链、Cargo 输入、补丁顺序及 ELF 哈希。`third_party/crosvm/patches/series` 和每个补丁说明用途、来源及上游状态。`tools/windows.lock.json` 固定 UEFI 源码、工具及签名驱动。`assets/` 仅保存文本声明和文件清单。`tools/prebuilt.lock.json` 固定 GunyahVM 自己的发布输入包；签名驱动直接取自锁定的上游发布。下载及缓存输入都在使用前校验，`tools/assets.lock.json` 校验文本声明。
