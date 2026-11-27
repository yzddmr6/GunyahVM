<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Building GunyahVM

This guide explains how to assemble the Debian packages from pinned inputs, check the source tree and prepare corresponding source for distribution.

## Assemble the release packages

On Linux, install Git, Python 3.11 or newer, GNU tar, zstd and `dpkg-deb`. Package assembly uses prebuilt ARM64 inputs; it does not compile crosvm, firmware, the guest kernel or graphics libraries.

```sh
python3 tools/build-release.py
```

The command builds the checked-out commit (`HEAD`), excluding uncommitted changes, and writes `dist/<version>/`. The output holds the application deb, `manifest.json`, the corresponding source and `SHA256SUMS`, plus every locked input whose URL names this version's release (the macOS kit, the macOS host archive, the Casilda packages): those are the assets this release publishes. An existing output directory must be moved or removed before rebuilding.

Inputs come from the repository's lock files. Downloads are shared between worktrees in `~/.cache/gunyahvm-build/<sha256>/<filename>`; use `--cache DIR` to choose another location. `tools/release.lock.json` pins the source archive supplying upstream inputs and the Cargo cache, plus the Casilda packages. The source bundler checks those inputs against the component locks and takes maintained source from `HEAD`. Inputs without a download URL must already exist at their hash-addressed cache path. The currently locked Casilda development deb has not been published, so it must be supplied in that cache before the first build.

On a supported ARM64 host, install the application and its two Casilda runtime dependencies together (replace `<version>` with the generated directory):

```sh
sudo apt install ./dist/<version>/gunyahvm_*_arm64.deb ./dist/<version>/gir1.2-casilda-1.0_*_arm64.deb ./dist/<version>/libcasilda-1.0_*_arm64.deb
```

The embedded viewer also needs wlroots 0.20. `tools/build-casilda.py` rebuilds Casilda from its locked sources on an ARM64 build system with the declared build dependencies installed.

Use `dpkg-deb --info PACKAGE.deb` and `dpkg-deb --contents PACKAGE.deb` to inspect a package without installing it. The [runtime component interface](runtime-components.md) defines the runtime manifest and the optional `--runtime-root ROOT` staging layout.

## Source inputs and rebuilds

The repository stores application source, patches, licenses and documentation images. Executable payloads are fetched as pinned build inputs. Each lock file records the hashes used for package assembly; `third_party/runtime/sources.json` records graphics source archives, patches, toolchains and build options. [NOTICE](../NOTICE) records component origins and attribution.

To rebuild baseline crosvm, prepare the compiler versions specified in `tools/crosvm.lock.json` and a Cargo source cache from the corresponding-source archive:

```sh
python3 tools/fetch-crosvm-inputs.py --output inputs
python3 tools/build-crosvm.py --inputs inputs --cargo-cache cargo-cache --rust-toolchain /path/to/rust-1.88.0 --output build/crosvm
```

This route uses the pinned crosvm/minijail sources, ordered patch queue and Cargo lock, with `--frozen --offline`. It checks the resulting ELF against the locked hash. The baseline has `gunyah,net,vnc` enabled; default package assembly overlays the graphics variant, which also enables `virgl_renderer`. The graphics variant's recipe and artifact hashes are recorded separately in `third_party/runtime/sources.json` and `tools/runtime.lock.json`.

`tools/build-runtime.py --input-cache inputs/prebuilt --output build/runtime` assembles the default runtime staging tree from the locked binary archive. It does not compile the upstream projects. The guest initrd recipe is `tools/build-guest-initrd.sh IMAGE OUTPUT`, run inside the pinned Ubuntu ARM64 environment with matching modules installed. Runtime assembly checks fixed input and output hashes; it does not promise byte-identical upstream recompilation.

The macOS host runtime (Droid-VM crosvm, LibVNCServer and Droid-VM virglrenderer) is built from the archives and patches locked under `host_sources` in `tools/macos.lock.json`. The corresponding-source archive carries those archives in `macos-sources/`:

```sh
python3 tools/build-macos-host.py --sources macos-sources --sysroot /path/to/arm64-sysroot \
  --rust-toolchain /path/to/rust-1.88.0 --host-tools /path/to/llvm-18-root --meson /path/to/meson-1.9.0 \
  --output build/macos-host
```

The builder maps every build path to a fixed reference path, collects the licence texts of each linked component and Rust crate, and writes the release input archive with its member hashes. No Apple file is an input or an output.

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
python3 tools/bundle-virtualization-source.py --inputs inputs --cargo-cache cargo-cache --runtime-sources inputs/runtime-sources --macos-sources macos-sources --output gunyahvm-1.6.1-source.tar.zst
```

Supply pinned upstream archives and the Cargo registry source cache. The bundler verifies archive hashes and crate checksums, includes maintained source, patches, licenses and build scripts, and excludes executable payloads, Git metadata, build products and logs. Source archives for guest Linux, virglrenderer and Mesa are mandatory and checked against `third_party/runtime/sources.json`; the macOS host source archives are checked against `tools/macos.lock.json`.

Review the generated source manifest and distribute the corresponding-source archive with the binary packages. Preserve upstream copyright and license files. The build tools and CI produce artifacts; they do not publish a release.

## Windows installation payload

`tools/windows.lock.json` pins the ARM64 OpenSSH ZIP version, source and SHA256. The offline cache must also contain `OpenSSH-ARM64.zip`. Package assembly and installation-media preparation verify it and retain upstream licences. Guest first configuration installs the service locally without fetching Windows Update features. Put `pwsh` on `PATH` to run the PowerShell script tests. Actual Windows installation and control still require device validation.

The Windows GPU driver is the unchanged Gl1ch5 build-43 package (WDDM driver, D3D11 and D3D12 user-mode drivers, Vulkan loader and Turnip), except `vulkan_freedreno.dll` and `dx11um_virtio.dll`. `vulkan_freedreno.dll` is rebuilt from the same Droid-VM Mesa commit, patches, llvm-mingw toolchain and Meson options, plus the Adreno 830 revision 1 chip ID in `third_party/windows/gpu/`. `dx11um_virtio.dll` is rebuilt from the same virtio-d3d11 and DXVK commits, Gl1ch5 patch and toolchain, plus the DXVK change in `third_party/windows/gpu/` that lowers min-precision shader values to 16-bit types only when the GPU supports 16-bit shader inputs and outputs. `gpu_driver`, `gpu_turnip` and `gpu_d3d11` in `tools/windows.lock.json` record the URLs, hashes and recipes.
