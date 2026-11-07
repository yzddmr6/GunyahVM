<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# 运行组件接口

运行组件包安装 `/usr/share/gunyahvm/runtime.json`。schema 为 `1`，architecture 为 `arm64`。每个组件提供非空 `version`、字符串数组 `features`、以及将文件角色映射到绝对安装路径的 `files` 对象。路径必须位于 `/usr/lib/gunyahvm/` 或 `/usr/share/gunyahvm/` 下；缺失文件、越界路径和未知 schema 会被拒绝。清单声明构建能力，不代表某台宿主已经运行成功。

| 组件 | 必需文件角色 | 能力名称及元数据 |
| --- | --- | --- |
| `crosvm` | `binary` = `/usr/lib/gunyahvm/crosvm/crosvm` | `gunyah`、`net`、`vhost-net`、`simplefb`、`vnc`、`p9`、`gpu`、`runtime-share`、`shared-memory`、`balloon`、`input-modes`；`build_features` 记录实际 Cargo 特性 |
| `renderer` | `library`；Venus 另需 `render_server` | `virgl`、`venus`、`drm-msm`；库目录仅供 VMM 私有使用 |
| `guest_kernel` | `image`、`initrd`；可选模块 tar 包 `modules` | `virtio-gpu`、`guest-accept`、`shared-boot`；精确 `kernel_release`，可选 `modules_load` 列表 |
| `guest_mesa` | `archive` | `freedreno-virtio`；如 `ubuntu:26.04` 的 `guest_abi`，相对路径 `library_subdir` 与 `icd` |
| `uefi` | `image` | `windows-ecam` |
| `windows_drivers` | `inventory`、`certificate` | `test-signed`；清单和驱动/helper 文件放在同一 Windows 资产目录 |

来宾 initrd 必须包含挂载根磁盘及启动网络所需驱动。模块包路径相对于 `/usr/lib/modules/<kernel_release>/`；Mesa 包路径相对于 `/usr/lib/gunyahvm/guest-mesa/`，其 ICD 引用该最终私有前缀。来宾 agent 启用 native Mesa 前核对系统及版本，并安装显式选择的 GunyahVM native 桌面会话和 `gunyahvm-graphics-run --native COMMAND`。它们只为自己的进程设置私有 Mesa 路径；GNOME 激活服务仅在该登录会话期间接收相同环境。离开 native 模式时移除该会话选择，不修改发行版库、全局 loader 配置、全局 ICD 或 alternatives。

图形模式由清单、可访问的 DRM 设备与宿主 Gunyah ABI 取交集。Venus/native 需要 `GH_CAP_PROTECTED_SHARE`、crosvm 运行时共享及匹配的 GuestAccept 来宾；native 另需 msm 渲染节点和匹配的来宾 Mesa。共享内存须显式选择，不允许 native 图形。选择这些模式前，VM 配置必须启用配套运行组件来宾内核。缺少 protected share 时仍可保留普通 virgl。普通 virgl 显式关闭 GuestAccept。Venus/native 的 GuestAccept 设备运行于单个普通用户 crosvm 进程，Venus 另外启动声明的普通用户 render server；其他模式保留 crosvm 设备沙箱。仅在构建实现对应选项时传入禁用 balloon 的参数。

可通过 `tools/build-virtualization-debs.py --runtime-root ROOT` 接入运行组件暂存根。它须包含清单、所有声明的载荷，以及保存来源和许可证的 `usr/share/doc/gunyahvm-runtime/`。私有库所需的每个依赖文件均须声明为额外文件角色；Windows 驱动清单引用的驱动/helper 也须逐个声明。构建器把这些组件覆盖到 Windows 组合中并按组件拆包；未传此选项时，由 `tools/runtime_inputs.py` 装配锁定的默认图形输入，不会仅因某个构建目录存在就推断或启用图形能力。

## 默认软件包组合

四个包使用相同的精确 Debian 修订号 `1.0.0-1`。`gunyahvm` 依赖 `gunyahvm-runtime (= 1.0.0-1)`，后者再精确依赖同修订号的 `gunyahvm-guest-kernel` 和 `gunyahvm-guest-mesa`。应用拥有完整清单及 Windows 资产，三个载荷包分别拥有互不重叠的文件。存在运行中 VM 时，各包的移除/升级守卫都会拒绝变更。应一次安装全部四个本地 deb。

默认载荷为 crosvm（`gunyah,net,vnc,virgl_renderer`）、启用 MSM 与 Venus 的私有 virglrenderer 1.3.0、Linux 7.2.15-gvm61 及通用 dracut initrd/模块、面向 Ubuntu 26.04 arm64 的 Freedreno virtio Mesa 26.2.5。宿主库通过包依赖解析；VMM/server 的 RUNPATH 使用 `$ORIGIN`，应用显式提供清单中的私有 server 路径。不更换宿主内核包或全局 renderer 选择。

`tools/runtime.lock.json` 锁定输入归档、逐项输入及每个安装载荷。`third_party/runtime/sources.json` 固定源码 commit/归档、补丁顺序、构建选项、工具链、目标包哈希以及重定位前后的产物。这是“输入与产物均锁定”的装配路线，不声称所有上游项目都能逐字节重复编译。`tools/build-runtime.py --input-cache CACHE --output ROOT` 离线生成暂存契约；打包器会自动执行。同二进制分发的还有对应源码归档。
