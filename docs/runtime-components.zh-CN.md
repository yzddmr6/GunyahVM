<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# 运行组件接口

运行组件包安装 `/usr/share/gunyahvm/runtime.json`。schema 为 `1`，architecture 为 `arm64`。每个组件提供非空 `version`、字符串数组 `features`、以及将文件角色映射到绝对安装路径的 `files` 对象。路径必须位于 `/usr/lib/gunyahvm/` 或 `/usr/share/gunyahvm/` 下；缺失文件、越界路径和未知 schema 会被拒绝。清单声明构建能力，不代表某台宿主已经运行成功。

| 组件 | 必需文件角色 | 能力名称及元数据 |
| --- | --- | --- |
| `crosvm` | `binary` = `/usr/lib/gunyahvm/crosvm/crosvm` | `gunyah`、`net`、`vhost-net`、`simplefb`、`vnc`、`p9`、`gpu`、`runtime-share`、`shared-memory`、`balloon`、`input-modes`；`build_features` 记录实际 Cargo 特性 |
| `renderer` | `library` | `virgl`、`venus`、`drm-msm`；库目录仅供 VMM 私有使用 |
| `guest_kernel` | `image`、`initrd`；可选模块 tar 包 `modules` | `virtio-gpu`、`guest-accept`、`shared-boot`；精确 `kernel_release`，可选 `modules_load` 列表 |
| `guest_mesa` | `archive` | `freedreno-virtio`；如 `ubuntu:26.04` 的 `guest_abi`，相对路径 `library_subdir` 与 `icd` |
| `uefi` | `image` | `windows-ecam` |
| `windows_drivers` | `inventory`、`certificate` | `test-signed`；清单和驱动/helper 文件放在同一 Windows 资产目录 |

来宾 initrd 必须包含挂载根磁盘及启动网络所需驱动。模块包路径相对于 `/usr/lib/modules/<kernel_release>/`；Mesa 包路径相对于 `/usr/lib/gunyahvm/guest-mesa/`，其 ICD 引用该最终私有前缀。来宾 agent 启用 native Mesa 前核对来宾系统及版本；离开 native 模式时移除自己的 loader/ICD 配置，不覆盖发行版 Mesa 文件。

图形模式由清单、可访问的 DRM 设备与宿主 Gunyah ABI 取交集。Venus/native 需要 `GH_CAP_PROTECTED_SHARE`、crosvm 运行时共享及匹配的 GuestAccept 来宾；native 另需 msm 渲染节点和匹配的来宾 Mesa。共享内存须显式选择，不允许 native 图形。选择这些模式前，VM 配置必须启用配套运行组件来宾内核。缺少 protected share 时仍可保留普通 virgl。GuestAccept 使用单个普通用户 crosvm 进程；其他模式保留 crosvm 设备沙箱。仅在构建实现对应选项时传入禁用 balloon 的参数。

可通过 `tools/build-virtualization-debs.py --runtime-root ROOT` 接入运行组件暂存根。它须包含清单、所有声明的载荷，以及保存来源和许可证的 `usr/share/doc/gunyahvm-runtime/`。私有库所需的每个依赖文件均须声明为额外文件角色；Windows 驱动清单引用的驱动/helper 也须逐个声明。构建器把这些组件覆盖到默认 crosvm/Windows 组合中，不会仅因某个构建目录存在就推断或启用图形能力。
