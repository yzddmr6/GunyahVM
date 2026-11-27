<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Building GunyahVM

This guide explains how to assemble the Debian packages from pinned inputs, check the source tree and prepare corresponding source for distribution.

## Assemble the release packages

On Linux, install Git, Python 3.11 or newer, GNU tar, zstd and `dpkg-deb`. Package assembly uses prebuilt ARM64 inputs; it does not compile crosvm, firmware, the guest kernel or graphics libraries.

```sh
python3 tools/build-release.py
```

The command builds the checked-out commit (`HEAD`), excluding uncommitted changes, and writes `dist/<version>/`. The output holds the application deb, `manifest.json`, the corresponding source and `SHA256SUMS`, plus every locked input whose URL names this version's release (the runtime input, source inputs, macOS kit and Casilda packages): those are the assets this release publishes. An existing output directory must be moved or removed before rebuilding.

Inputs come from the repository's lock files. Downloads are shared between worktrees in `~/.cache/gunyahvm-build/<sha256>/<filename>`; use `--cache DIR` to choose another location. `tools/release.lock.json` pins the source archive supplying upstream inputs and the Cargo cache, plus the Casilda packages. The source bundler checks those inputs against the component locks and takes maintained source from the checked-out commit. Inputs without a download URL must already exist at their hash-addressed cache path. The currently locked Casilda development deb has not been published, so it must be supplied in that cache before the first build.

On a supported ARM64 host, install the application and its two Casilda runtime dependencies together (replace `<version>` with the generated directory):

```sh
sudo apt install ./dist/<version>/gunyahvm_*_arm64.deb ./dist/<version>/gir1.2-casilda-1.0_*_arm64.deb ./dist/<version>/libcasilda-1.0_*_arm64.deb
```

The embedded viewer also needs wlroots 0.20. `tools/build-casilda.py` rebuilds Casilda from its locked sources on an ARM64 build system with the declared build dependencies installed.

Use `dpkg-deb --info PACKAGE.deb` and `dpkg-deb --contents PACKAGE.deb` to inspect a package without installing it. The [runtime component interface](runtime-components.md) defines the runtime manifest and the optional `--runtime-root ROOT` staging layout.

## Source inputs and rebuilds

The repository stores application source, patches, licenses and documentation images. Executable payloads are fetched as pinned build inputs. Each lock file records the hashes used for package assembly; `third_party/runtime/sources.json` records graphics source archives, patches, toolchains and build options. [NOTICE](../NOTICE) records component origins and attribution.

To rebuild the shared crosvm, virglrenderer and LibVNCServer, prepare the compiler versions in `tools/crosvm.lock.json`, Meson 1.9.2, and the source/Cargo inputs from the corresponding-source archive:

```sh
python3 tools/fetch-crosvm-inputs.py --output inputs
python3 tools/build-crosvm.py --inputs inputs --cargo-cache cargo-cache \
  --rust-toolchain /path/to/rust-1.88.0 --runtime-sources runtime-sources \
  --meson /path/to/meson-1.9.2 --output build/crosvm
```

The builder applies one 53-patch crosvm queue and builds with `--frozen --offline`, at most eight jobs. It constructs the sysroot from pinned development debs. Features are `gunyah,net,vnc,virgl_renderer,wayland_gpu_blit,virtio_wl`. The single virglrenderer 1.3.0 has five patches and enables MSM and Venus; LibVNCServer retains poll-based socket handling. The renderer archive already contains the first four patches; its lock records that fact, and the builder applies the fifth. All output ELF hashes are locked. `macos` is now only the boot kit component.

Export both the crosvm and renderer queues with `git format-patch --zero-commit --no-numbered --no-signature --full-index -o <queue> <base>..<tip>`, retaining existing filenames. These options omit commit IDs, patch totals and Git version signatures so other patches stay unchanged when the queue grows.

`tools/build-runtime.py --input-cache inputs/prebuilt --output build/runtime` assembles the hash-locked runtime input. Guest kernel, initrd and Mesa assets are retained. The corresponding-source archive contains the application source from the same checked-out commit.

The macOS boot kit (`gunyahvm-macos-kit-1.6.0.tar.zst`, pinned file by file under `kit` in `tools/macos.lock.json`) is built from public sources in two steps:

```sh
python3 tools/build-macos-kit-inputs.py --workspace build/macos-droidvm --apple-kc kc27.bin \
  --fdt board.dtb --output kit-sources.json
python3 tools/build-macos-kit.py --sources kit-sources.json --output build/macos-kit
```

`tools/build-macos-kit-inputs.py` compiles the XNU kernel, the board kexts, the Metal plugin with its Turnip driver and Shader Cache app, the UniXNU and DarwinBoot loaders, and the EDK2 firmware (Droid-VM/edk2-gunyah with `third_party/macos/kit/edk2-gunyah-virtio-gpu-queue-size.patch`). It checks out [Droid-VM/macOS-DroidVM](https://github.com/Droid-VM/macOS-DroidVM) at the commit pinned under `kit` `source`, applies the changes in `third_party/macos/kit/` (XNU's large-memory layout with 4 KiB page tables, so the guest can have 9 GiB, and the 3D pipe in the GPU submissions of Turnip's virtio backend, which the host's msm driver requires), and runs that repository's own build environment, `scripts/setup-env.py` and `scripts/product.py`, which fetch every source and tool at the commits and hashes of its `configs/env.lock.json`. The steps run in a container made from `third_party/macos/kit/Dockerfile`, Ubuntu 26.04 at a fixed archive snapshot, so Docker must work for the current user; the first build downloads about 5 GB. `--apple-kc` is Apple's 26B123 arm64 kernel collection (decoded from the IPSW's `kernelcache.release.vma2`), which the display kext's ABI generator reads; no Apple file reaches an output. Every output is the same file on every build: the XNU and EDK2 builds take their dates from `SOURCE_DATE_EPOCH` (with `third_party/macos/kit/xnu-source-date-epoch.patch` and `edk2-reproducible.patch`, which also derives EDK2's stack cookies from it), and no build path reaches an output. `--save-mirror DIR` archives what the build fetched from small community repositories, and `--mirror DIR` restores it into a new workspace.

`kit-sources.json` names the built files and the board device tree, which crosvm writes for the macOS profile (`--dump-device-tree-blob`); their hashes are in the lock. `tools/build-macos-kit.py` then compiles the helper VM's apfs.ko (for the packaged 7.2.15-gvm64 guest kernel, with its imports checked strictly against the `Module.symvers` of a full build of that kernel) and an arm64 mkapfs from the sources locked in `macos-sources/`, and adds Ubuntu's GNU tar. The kit holds no Apple file; the device downloads the rest from Apple when it creates macOS.

## Check a change

```sh
python3 tools/lint.py
python3 -m pytest -q tools/tests
```

Lint checks syntax, SPDX annotations, local documentation links, matching English/Chinese heading structures and pinned inputs. The source-only tests run offline and deselect tests that require binary payloads. With a verified input cache, also run:

```sh
python3 -m pytest -q tools/tests --input-cache inputs/prebuilt
```

The cache enables package integration and compiled-helper tests. A clean Ubuntu 26.04 ARM64 rootfs under qemu-user can check dependency installation. VM execution, graphics and desktop-seat permissions require a supported Gunyah device; a successful package build does not establish those capabilities.

## Distribute corresponding source

```sh
python3 tools/bundle-virtualization-source.py --inputs inputs --cargo-cache cargo-cache --runtime-sources inputs/runtime-sources --macos-sources macos-sources --output gunyahvm-1.7.0-source.tar.zst
```

Supply pinned upstream archives and the Cargo registry source cache. The bundler verifies archive hashes and crate checksums, includes maintained source, patches, licenses and build scripts, and excludes executable payloads, Git metadata, build products and logs. Source archives for guest Linux, virglrenderer and Mesa are mandatory and checked against `third_party/runtime/sources.json`; the macOS host source archives are checked against `tools/macos.lock.json`.

Review the generated source manifest and distribute the corresponding-source archive with the binary packages. Preserve upstream copyright and license files. The build tools and CI produce artifacts; they do not publish a release.

## Windows installation payload

`tools/windows.lock.json` pins the ARM64 OpenSSH ZIP version, source and SHA256. The offline cache must also contain `OpenSSH-ARM64.zip`. Package assembly and installation-media preparation verify it and retain upstream licences. Guest first configuration installs the service locally without fetching Windows Update features. Put `pwsh` on `PATH` to run the PowerShell script tests. Actual Windows installation and control still require device validation.

The Windows GPU driver is the unchanged Gl1ch5 build-43 package (WDDM driver, D3D11 and D3D12 user-mode drivers, Vulkan loader and Turnip), except `vulkan_freedreno.dll` and `dx11um_virtio.dll`. `vulkan_freedreno.dll` is rebuilt from the same Droid-VM Mesa commit, patches, llvm-mingw toolchain and Meson options, plus the Adreno 830 revision 1 chip ID in `third_party/windows/gpu/`. `dx11um_virtio.dll` is rebuilt from the same virtio-d3d11 and DXVK commits, Gl1ch5 patch and toolchain, plus the DXVK change in `third_party/windows/gpu/` that lowers min-precision shader values to 16-bit types only when the GPU supports 16-bit shader inputs and outputs. `gpu_driver`, `gpu_turnip` and `gpu_d3d11` in `tools/windows.lock.json` record the URLs, hashes and recipes.
