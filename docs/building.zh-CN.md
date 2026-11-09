<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# 构建 GunyahVM

本文说明如何用固定输入组装 Debian 软件包、检查源码，以及准备分发所需的对应源码。

## 组装发布软件包

在 Linux 上安装 Python 3.11 及以上版本、GNU tar、zstd 和 `dpkg-deb`。软件包组装使用预编译的 ARM64 输入，不编译 crosvm、固件、来宾内核或图形库。

```sh
python3 tools/fetch-prebuilt-inputs.py --cache inputs/prebuilt
python3 tools/build-virtualization-debs.py --input-cache inputs/prebuilt --offline --output dist
```

下载步骤获取应用输入归档、图形运行组件归档、上游签名驱动归档和 ARM64 OpenSSH 离线安装包。URL 与哈希分别固定在 `tools/prebuilt.lock.json`、`tools/runtime.lock.json` 和 `tools/windows.lock.json` 中，也可自行将完全相同的归档放入缓存。离线构建遇到缺失输入或校验不符会失败。不加 `--offline` 时，构建器会下载缺失输入。

输出包含四个 `dist/gunyahvm*_1.0.1-1_arm64.deb` 软件包、暂存目录、文件清单和 `SHA256SUMS`。在受支持的 ARM64 宿主上一起安装四个包：

```sh
sudo apt install ./dist/gunyahvm*_arm64.deb
```

可用 `dpkg-deb --info PACKAGE.deb` 和 `dpkg-deb --contents PACKAGE.deb` 查看软件包，无需安装。[运行组件接口](runtime-components.zh-CN.md)定义软件包拆分和可选的 `--runtime-root ROOT` 暂存布局。

## 源码输入与重建

仓库存放应用源码、补丁、许可证和文档图片。可执行组件作为固定构建输入下载。各锁定文件记录组装使用的哈希，`third_party/runtime/sources.json` 记录图形组件的源码归档、补丁、工具链和构建选项。[NOTICE](../NOTICE)记录组件来源和作者声明。

重建基线 crosvm 前，准备 `tools/crosvm.lock.json` 指定版本的编译器，以及对应源码归档中的 Cargo 源码缓存：

```sh
python3 tools/fetch-crosvm-inputs.py --output inputs
python3 tools/build-crosvm.py --inputs inputs --cargo-cache cargo-cache --rust-toolchain /path/to/rust-1.88.0 --output build/crosvm
```

此路径使用固定的 crosvm/minijail 源码、有序补丁队列和 Cargo 锁文件，以 `--frozen --offline` 构建，并检查生成的 ELF 是否符合锁定哈希。基线启用 `gunyah,net,vnc`；默认打包会覆盖为另启用 `virgl_renderer` 的图形版本。图形版本的配方与产物哈希分别记录在 `third_party/runtime/sources.json` 和 `tools/runtime.lock.json` 中。

`tools/build-runtime.py --input-cache inputs/prebuilt --output build/runtime` 从固定二进制归档组装默认运行组件暂存目录，不编译上游项目。来宾 initrd 配方为 `tools/build-guest-initrd.sh IMAGE OUTPUT`，须在固定的 Ubuntu ARM64 环境中运行，并已安装匹配模块。运行组件组装检查固定输入和输出的哈希，不承诺上游重新编译后逐字节一致。

## 检查改动

```sh
python3 tools/lint.py
python3 -m pytest -q tools/tests
```

Lint 检查语法、SPDX 声明、本地文档链接、中英文标题结构和固定输入。纯源码测试离线运行，跳过需要二进制组件的测试。具备已校验的输入缓存后，再运行：

```sh
python3 -m pytest -q tools/tests --input-cache inputs/prebuilt
```

缓存启用打包集成和编译助手测试。可在 qemu-user 下的干净 Ubuntu 26.04 ARM64 根文件系统中检查依赖安装。虚拟机运行、图形和桌面会话权限需要受支持的 Gunyah 设备，构建成功不能证明这些能力。

## 分发对应源码

```sh
python3 tools/bundle-virtualization-source.py --inputs inputs --cargo-cache cargo-cache --runtime-sources inputs/runtime-sources --output gunyahvm-1.0.1-source.tar.zst
```

提供固定的上游归档和 Cargo registry 源码缓存。打包器验证归档哈希与 crate 校验值，包含维护的源码、补丁、许可证和构建脚本，排除可执行组件、Git 元数据、构建产物和日志。来宾 Linux、virglrenderer 和 Mesa 源码归档是必需输入，按 `third_party/runtime/sources.json` 校验。

检查生成的源码清单，将对应源码归档随二进制软件包一起分发，保留上游版权和许可证文件。构建工具和 CI 生成产物，不发布 Release。

## Windows 安装载荷

`tools/windows.lock.json` 固定 ARM64 OpenSSH ZIP 的版本、来源和 SHA256。离线缓存还须包含 `OpenSSH-ARM64.zip`。打包与安装介质准备均校验该包，保留上游许可证；来宾首次配置在本地安装服务，不通过 Windows Update 下载功能。将 `pwsh` 放入 `PATH` 可运行 PowerShell 脚本测试。Windows 的实际安装和控制仍需设备验证。
