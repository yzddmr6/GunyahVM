<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# 构建 GunyahVM

## 组装软件包

在 Linux 上使用 Python 3.11 或更新版本、GNU tar、zstd 和 `dpkg-deb`。Git 仓库只保存源码和文本。打包器按 `tools/prebuilt.lock.json` 中的 URL 和 SHA-256，从 [GunyahVM v1.0.0](https://github.com/yzddmr6/GunyahVM/releases/tag/v1.0.0) 获取 `gunyahvm-1.0.0-inputs.tar.zst`，并按 `tools/windows.lock.json` 直接从 Droid-VM 获取原始签名驱动 ZIP。组装软件包时不编译 crosvm 或固件。

```sh
python3 tools/fetch-prebuilt-inputs.py --cache inputs/prebuilt
python3 tools/build-virtualization-debs.py --input-cache inputs/prebuilt --offline --output dist
```

省略单独下载步骤和 `--offline` 时，打包器会自动下载缺失的输入。每次调用都会检查缓存；哈希不符会使构建失败，不会静默替换损坏的缓存。下载内容校验通过后才写入缓存。发布输入包必须恰好包含锁定的二进制与声明文件，大小与哈希均须一致；拒绝链接、重复条目和额外路径。上游 ZIP 仅提取选定且逐文件校验的驱动。文本许可证与清单也会校验。

输出为 `dist/gunyahvm_1.0.0_arm64.deb`、暂存目录、逐文件清单和 SHA256SUMS。用 `dpkg-deb --info` 和 `dpkg-deb --contents` 审查。Windows 所需依赖均为强制包依赖。

首次发布前，将本地准备好的输入附件及锁定的 `gunyah-arm64-drivers.zip` 放入缓存，使用 `--offline`，校验规则完全相同；这些工具不会创建 GitHub Release。获准发布后，将输入附件与 Debian 包及对应源码一同发布。CI 打包任务会下载该附件，因此需要附件已发布；lint 与纯源码离线测试可在发布前运行。CI 仅上传构建产物，不发布 Release。

## 二进制来源

| 载荷 | 源码与构建 |
| --- | --- |
| `crosvm` | ChromiumOS crosvm `96e84c3f680e34e82e932ef911ae97d734121758`、minijail `7845d89927a82dbdcdee6276837cd0978f69ba19`；iceland-gvm `7ae498c24e61a8860740d003572455a7443eecd3` 的 0001–0030 补丁，以及自维护的 0031（关闭通道时减少噪声）和 0032（限制子进程退出等待）。完整顺序见 `third_party/crosvm/patches/series`，每个补丁保留用途、来源和上游状态。使用 Rust/Cargo 1.88.0、GCC 13.3.0 ARM64 交叉编译器、binutils 2.42、LLVM/libclang 18.1.3，以及 `tools/crosvm.lock.json` 锁定的 Ubuntu 26.04 ARM64 sysroot；关闭默认特性，仅启用 `gunyah,net,vnc`。 |
| `windows/firmware.fd` | Droid-VM edk2-gunyah `90c4d52c7d11485318bbb4fb9291a15ea1550ab2`、TianoCore edk2 `b7a715f7c03c45c6b4575bf88596bfd79658b8ce`、固定子模块及 `0005-VirtioBlkDxe-bound-DMA-requests-to-4MiB.patch`。RELEASE 构建使用锁定的 GCC 13.3.0 交叉编译器、binutils 2.42、ACPICA/IASL 20230628 和 Python 3.12.3；完整版本和 stack-cookie 输入见 `tools/windows.lock.json`。 |
| `windows/winpe-helper.exe` | 自有 MIT 源码 `src/gunyahvm/windows/winpe-helper.c`，继承自 yzddmr6 的小米平板 8 Pro 主线移植项目，为 GunyahVM 来宾路径重编。使用 Clang/LLD/llvm-dlltool 18.1.3 构建独立 ARM64 PE，不依赖 Windows SDK 或签名密钥；参数记录在 `tools/build-winpe-helper.py`。 |
| 驱动与公开证书 | [Droid-VM 签名驱动发布附件](https://github.com/Droid-VM/gunyah-guest-drivers-windows/releases/download/dev/gunyah-arm64-drivers.zip)，固定附件 ID `558946077`，源码 `9922545ee7c65927e720c3c12b39cd8ae144e90b`。原样选取 `rdmapool`、`pvmpower`、`viostor`、`NetKVM`、`vioinput` 和 `DroidVM_Test.cer`；virtio 驱动源自 [virtio-win](https://github.com/virtio-win/kvm-guest-drivers-windows)。`assets/windows/driver-files.json` 逐文件记录来源、许可证与 SHA-256。直接从上游下载，不重新装入 GunyahVM 输入附件。公开测试证书不代表生产签名背书。 |

crosvm 与固件字节复用来源公开项目的 1.0.0 虚拟机组件，本仓库保留完整构建方法与哈希。二进制内原有的中性构建路径和版本标记属于来源信息；运行时路径使用 `gunyahvm`，并显式覆盖 crosvm pivot root。`NOTICE`、DEP-5 copyright、`assets/licenses/` 和 `assets/windows/licenses/` 保留原始署名。发布输入包包含表中前三项，以及 `notices/` 下的原始组件许可证和 NOTICE；所有文件的哈希及大小记录在 `tools/prebuilt.lock.json`。

## 重编 crosvm

准备 `tools/crosvm.lock.json` 指定的精确编译器版本，以及对应源码附件中的 Cargo 源码缓存。上游归档、sysroot、工具链输入和每个补丁均已固定。重编所需磁盘远多于组装软件包。

```sh
python3 tools/fetch-crosvm-inputs.py --output inputs
python3 tools/build-crosvm.py --inputs inputs --cargo-cache cargo-cache --rust-toolchain /path/to/rust-1.88.0 --output build/crosvm
```

构建使用 `--frozen --offline`、锁定的 Cargo.lock、SOURCE_DATE_EPOCH 和中性参考路径。最终 ELF 必须匹配锁定哈希。保留原始补丁说明与协作署名。

## 重编 Windows 组件

```sh
python3 tools/fetch-windows-inputs.py --output windows-inputs
python3 tools/build-windows-assets.py --inputs windows-inputs --firmware-cache windows-inputs/firmware --output build/windows
python3 tools/build-virtualization-debs.py --crosvm-build build/crosvm --windows-build build/windows --output rebuilt-package
```

固件在 bubblewrap 命名空间中构建。小型助手也可单独用 `python3 tools/build-winpe-helper.py --tool-root EXTRACTED_LOCKED_LLVM --output winpe-helper.exe --work helper-work` 重编。为保留签名，驱动仍使用上游二进制。构建不使用 Windows ISO、签名密钥或设备。

使用 GNU tar 1.35 和 zstd 1.5.5，从锁定的构建输出重建包含三个二进制及其声明的相同输入附件：

```sh
python3 tools/bundle-prebuilt-inputs.py --crosvm build/crosvm/crosvm --firmware build/windows/assets/firmware.fd --winpe-helper build/windows/assets/winpe-helper.exe --output gunyahvm-1.0.0-inputs.tar.zst
```

归档工具按发布锁文件校验每个二进制与最终归档，不上传任何内容。

## 对应源码与验证

```sh
python3 tools/bundle-virtualization-source.py --inputs inputs --cargo-cache cargo-cache --output gunyahvm-1.0.0-source.tar.zst
python3 tools/lint.py
python3 -m pytest -q tools/tests
python3 -m pytest -q tools/tests --input-cache inputs/prebuilt
```

源码归档工具直接接收固定的上游源码归档和 Cargo registry 源码缓存，校验归档哈希及 Cargo.lock 中的 crate 校验和，加入维护源码、补丁、文本许可证和构建脚本，排除 Git 元数据、二进制、构建产物及日志。包含固件和驱动源码地址及获取脚本；还可用 `--windows-sources windows-inputs/firmware --driver-sources DRIVER_SOURCE_TREE` 加入采用宽松许可证的源码树，并核对固定提交。GPL 覆盖的 crosvm 及其依赖源码始终包含。分发前审查源码清单。

两个测试命令均离线运行。第一个运行全部纯源码测试，包括模拟下载、损坏缓存和归档边界测试，并报告未选中的载荷集成测试。第二个还通过已校验的本地缓存测试真实载荷打包、源码构建目录等价性及编译后助手。用干净的 Ubuntu 26.04 ARM64 rootfs 和 qemu-user 检查依赖安装与集成语法；硬件执行和座席切换需要 Gunyah 设备。

## 运行组件覆盖

构建器可通过 `--runtime-root ROOT` 接入暂存运行组件根，详见[清单契约](runtime-components.zh-CN.md)。包内含 GTK、CLI、设备档案及默认 Windows 资产。测试覆盖能力/档案匹配、后端参数、固定特权输入及生命周期边界，包括迁移的 iceland-vms 用例。`tools/tests/smoke_gui.py OUTPUT` 检查 GTK 页面，并在可用时使用上游截图渲染器。
