<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# 构建 GunyahVM

本文说明如何用固定输入组装 Debian 软件包、检查源码，以及准备分发所需的对应源码。

## 组装发布软件包

在 Linux 上安装 Git、Python 3.11 及以上版本、GNU tar、zstd 和 `dpkg-deb`。软件包组装使用预编译的 ARM64 输入，不编译 crosvm、固件、来宾内核或图形库。

```sh
python3 tools/build-release.py
```

命令构建当前检出的提交（`HEAD`），不包含未提交的改动，输出到 `dist/<版本>/`。输出包含应用 deb、`manifest.json`、对应源码和 `SHA256SUMS`，以及锁文件里 URL 指向本版本 release 的输入（macOS 套件、macOS 宿主归档、Casilda 软件包）：它们就是本版本要发布的资产。再次构建前需移走或删除已有输出目录。

输入来自仓库中的锁文件。各工作树共用 `~/.cache/gunyahvm-build/<sha256>/<文件名>` 下载缓存，可用 `--cache DIR` 指定其他位置。`tools/release.lock.json` 固定提供上游输入和 Cargo 缓存的源码归档，以及 Casilda 软件包。源码打包器按各组件的锁校验这些输入，维护的源码则取自 `HEAD`。没有下载 URL 的输入须预先放入对应摘要的缓存路径。目前锁定的 Casilda 开发包尚未发布，首次构建前须先提供该缓存文件。

在受支持的 ARM64 宿主上，将应用和两份 Casilda 运行依赖一起安装（将 `<版本>` 替换为生成的目录）：

```sh
sudo apt install ./dist/<版本>/gunyahvm_*_arm64.deb ./dist/<版本>/gir1.2-casilda-1.0_*_arm64.deb ./dist/<版本>/libcasilda-1.0_*_arm64.deb
```

应用内显示还需要 wlroots 0.20。`tools/build-casilda.py` 在装有所声明构建依赖的 ARM64 构建系统上，从锁定源码重建 Casilda。

可用 `dpkg-deb --info PACKAGE.deb` 和 `dpkg-deb --contents PACKAGE.deb` 查看软件包，无需安装。[运行组件接口](runtime-components.zh-CN.md)定义运行组件清单和可选的 `--runtime-root ROOT` 暂存布局。

## 源码输入与重建

仓库存放应用源码、补丁、许可证和文档图片。可执行组件作为固定构建输入下载。各锁定文件记录组装使用的哈希，`third_party/runtime/sources.json` 记录图形组件的源码归档、补丁、工具链和构建选项。[NOTICE](../NOTICE)记录组件来源和作者声明。

重建基线 crosvm 前，准备 `tools/crosvm.lock.json` 指定版本的编译器，以及对应源码归档中的 Cargo 源码缓存：

```sh
python3 tools/fetch-crosvm-inputs.py --output inputs
python3 tools/build-crosvm.py --inputs inputs --cargo-cache cargo-cache --rust-toolchain /path/to/rust-1.88.0 --output build/crosvm
```

此路径使用固定的 crosvm/minijail 源码、有序补丁队列和 Cargo 锁文件，以 `--frozen --offline` 构建，并检查生成的 ELF 是否符合锁定哈希。基线启用 `gunyah,net,vnc`；默认打包会覆盖为另启用 `virgl_renderer` 的图形版本。图形版本的配方与产物哈希分别记录在 `third_party/runtime/sources.json` 和 `tools/runtime.lock.json` 中。

`tools/build-runtime.py --input-cache inputs/prebuilt --output build/runtime` 从固定二进制归档组装默认运行组件暂存目录，不编译上游项目。来宾 initrd 配方为 `tools/build-guest-initrd.sh IMAGE OUTPUT`，须在固定的 Ubuntu ARM64 环境中运行，并已安装匹配模块。运行组件组装检查固定输入和输出的哈希，不承诺上游重新编译后逐字节一致。

macOS 宿主运行组件（Droid-VM crosvm、LibVNCServer 和 Droid-VM virglrenderer）由 `tools/macos.lock.json` 中 `host_sources` 锁定的源码归档和补丁构建，对应源码归档在 `macos-sources/` 中携带这些归档：

```sh
python3 tools/build-macos-host.py --sources macos-sources --sysroot /path/to/arm64-sysroot \
  --rust-toolchain /path/to/rust-1.88.0 --host-tools /path/to/llvm-18-root --meson /path/to/meson-1.9.0 \
  --output build/macos-host
```

构建器把所有构建路径映射到固定的参考路径，收集每个链接组件和 Rust crate 的许可证文本，并写出带成员哈希的发布输入归档。输入和产物中都没有 Apple 文件。

macOS 启动套件（`gunyahvm-macos-kit-1.6.0.tar.zst`，逐个文件锁在 `tools/macos.lock.json` 的 `kit`）分两步从公开源码构建：

```sh
python3 tools/build-macos-kit-inputs.py --workspace build/macos-droidvm --apple-kc kc27.bin \
  --fdt board.dtb --output kit-sources.json
python3 tools/build-macos-kit.py --sources kit-sources.json --output build/macos-kit
```

`tools/build-macos-kit-inputs.py` 编译 XNU 内核、板级 kext、Metal 插件及其 Turnip 驱动和 Shader Cache 应用、UniXNU 与 DarwinBoot 启动器，以及 EDK2 固件（Droid-VM/edk2-gunyah 加 `third_party/macos/kit/edk2-gunyah-virtio-gpu-queue-size.patch`）。它按 `kit` 的 `source` 锁定的提交检出 [Droid-VM/macOS-DroidVM](https://github.com/Droid-VM/macOS-DroidVM)，打上 `third_party/macos/kit/` 里的改动（XNU 的大内存布局配 4 KiB 页表，来宾才能用 9 GiB；Turnip 的 virtio 后端在 GPU 提交里写明 3D 管线，宿主的 msm 驱动要求这一项），再运行该仓库自己的构建环境 `scripts/setup-env.py` 和 `scripts/product.py`；它们按 `configs/env.lock.json` 里的提交和哈希获取每一份源码和工具。这些步骤在由 `third_party/macos/kit/Dockerfile` 构建的容器里运行（Ubuntu 26.04，固定的软件源快照），因此当前用户要能使用 Docker；第一次构建约下载 5 GB。`--apple-kc` 是 Apple 26B123 的 arm64 内核集合（从 IPSW 的 `kernelcache.release.vma2` 解出），显示 kext 的 ABI 生成器要读它；任何 Apple 文件都不会进入产物。每次构建的产物都逐字节相同：XNU 和 EDK2 的构建时间取自 `SOURCE_DATE_EPOCH`（`third_party/macos/kit/xnu-source-date-epoch.patch` 和 `edk2-reproducible.patch`，后者也用它生成 EDK2 的栈保护值），构建路径也不会进入任何产物。`--save-mirror DIR` 把构建从小型社区仓库取得的内容归档，`--mirror DIR` 把它恢复到新的工作区。

`kit-sources.json` 列出构建出的文件和板级设备树（crosvm 按 macOS 配置用 `--dump-device-tree-blob` 写出）；它们的哈希都在锁里。`tools/build-macos-kit.py` 再从 `macos-sources/` 里锁定的源码编译助手虚拟机的 apfs.ko（针对打包的 7.2.15-gvm64 来宾内核，按同一内核完整构建的 `Module.symvers` 严格检查导入）和 arm64 mkapfs，并放入 Ubuntu 的 GNU tar。套件里没有 Apple 文件；设备在创建 macOS 时从 Apple 下载其余部分。

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
python3 tools/bundle-virtualization-source.py --inputs inputs --cargo-cache cargo-cache --runtime-sources inputs/runtime-sources --macos-sources macos-sources --output gunyahvm-1.6.0-source.tar.zst
```

提供固定的上游归档和 Cargo registry 源码缓存。打包器验证归档哈希与 crate 校验值，包含维护的源码、补丁、许可证和构建脚本，排除可执行组件、Git 元数据、构建产物和日志。来宾 Linux、virglrenderer 和 Mesa 源码归档是必需输入，按 `third_party/runtime/sources.json` 校验；macOS 宿主源码归档按 `tools/macos.lock.json` 校验。

检查生成的源码清单，将对应源码归档随二进制软件包一起分发，保留上游版权和许可证文件。构建工具和 CI 生成产物，不发布 Release。

## Windows 安装载荷

`tools/windows.lock.json` 固定 ARM64 OpenSSH ZIP 的版本、来源和 SHA256。离线缓存还须包含 `OpenSSH-ARM64.zip`。打包与安装介质准备均校验该包，保留上游许可证；来宾首次配置在本地安装服务，不通过 Windows Update 下载功能。将 `pwsh` 放入 `PATH` 可运行 PowerShell 脚本测试。Windows 的实际安装和控制仍需设备验证。

Windows GPU 驱动原样使用 Gl1ch5 build-43 的驱动包（WDDM 驱动、D3D11 和 D3D12 用户态驱动、Vulkan 加载器和 Turnip），只有 `vulkan_freedreno.dll` 和 `dx11um_virtio.dll` 例外。`vulkan_freedreno.dll` 用同一个 Droid-VM Mesa 提交、同样的补丁、llvm-mingw 工具链和 Meson 选项重新编译，另加 `third_party/windows/gpu/` 里的 Adreno 830 第 1 版芯片 ID。`dx11um_virtio.dll` 用同样的 virtio-d3d11 和 DXVK 提交、Gl1ch5 的补丁和工具链重新编译，另加 `third_party/windows/gpu/` 里的 DXVK 修改：只有 GPU 支持 16 位着色器输入输出时，才把最低精度的着色器数值降为 16 位类型。URL、哈希和编译方法记在 `tools/windows.lock.json` 的 `gpu_driver`、`gpu_turnip` 和 `gpu_d3d11` 中。
