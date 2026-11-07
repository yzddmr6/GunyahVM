<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# Building GunyahVM

## Assemble the package

Use Python 3.11 or newer, GNU tar, zstd and `dpkg-deb` on Linux. The Git repository contains source and text only. The package builder fetches `gunyahvm-1.0.0-inputs.tar.zst` from [GunyahVM v1.0.0](https://github.com/yzddmr6/GunyahVM/releases/tag/v1.0.0), using the URL and SHA-256 in `tools/prebuilt.lock.json`. It fetches the original signed driver ZIP directly from Droid-VM using `tools/windows.lock.json`. No crosvm or firmware compilation occurs during package assembly.

```sh
python3 tools/fetch-prebuilt-inputs.py --cache inputs/prebuilt
python3 tools/build-virtualization-debs.py --input-cache inputs/prebuilt --offline --output dist
```

Omitting the separate fetch step and `--offline` lets the builder download missing inputs itself. Cached files are checked on every invocation. A checksum mismatch fails the build; it never silently replaces a bad cache entry. Downloaded files enter the cache only after verification. The release archive must contain exactly its locked binary and notice files, with matching sizes and hashes; links, duplicate members and unexpected paths are rejected. Only the selected, individually hashed driver files are extracted from the upstream ZIP. Text licences and inventories are also checked.

The output is `dist/gunyahvm_1.0.0_arm64.deb`, a staged tree, a file manifest and SHA256SUMS. Inspect it with `dpkg-deb --info` and `dpkg-deb --contents`. All Windows dependencies are mandatory package dependencies.

Before the first release exists, place the locally prepared input attachment and the locked `gunyah-arm64-drivers.zip` in the cache and use `--offline`. The same hash checks apply; no GitHub release is created by these tools. After approval, publish the exact input attachment alongside the Debian package and corresponding source. The package CI job downloads that attachment and will require its publication; lint and source-only offline tests work before publication. CI uploads build artifacts but does not publish releases.

## Binary origins

| Payload | Source and build |
| --- | --- |
| `crosvm` | ChromiumOS crosvm `96e84c3f680e34e82e932ef911ae97d734121758`, minijail `7845d89927a82dbdcdee6276837cd0978f69ba19`; iceland-gvm `7ae498c24e61a8860740d003572455a7443eecd3` patches 0001–0030, plus maintained patches 0031 (quiet closed shutdown channel) and 0032 (bounded child teardown). The full ordered queue, purpose, origin and upstream status are in `third_party/crosvm/patches/series` and each patch. Built with Rust/Cargo 1.88.0, GCC 13.3.0 ARM64 cross compiler, binutils 2.42, LLVM/libclang 18.1.3 and the Ubuntu 26.04 ARM64 sysroot locked in `tools/crosvm.lock.json`; features `gunyah,net,vnc`, default features disabled. |
| `windows/firmware.fd` | Droid-VM edk2-gunyah `90c4d52c7d11485318bbb4fb9291a15ea1550ab2`, TianoCore edk2 `b7a715f7c03c45c6b4575bf88596bfd79658b8ce`, pinned submodules and `0005-VirtioBlkDxe-bound-DMA-requests-to-4MiB.patch`. RELEASE build with the locked GCC 13.3.0 cross compiler, binutils 2.42, ACPICA/IASL 20230628 and Python 3.12.3; all versions and stack-cookie inputs are in `tools/windows.lock.json`. |
| `windows/winpe-helper.exe` | Project-owned MIT source `src/gunyahvm/windows/winpe-helper.c`, inherited from yzddmr6's Xiaomi Pad 8 Pro mainline port and rebuilt for GunyahVM guest paths. Freestanding ARM64 PE, Clang/LLD/llvm-dlltool 18.1.3, no Windows SDK or signing key; `tools/build-winpe-helper.py` records the flags. |
| Drivers and public certificate | [Droid-VM signed driver release](https://github.com/Droid-VM/gunyah-guest-drivers-windows/releases/download/dev/gunyah-arm64-drivers.zip), immutable asset ID `558946077`, source `9922545ee7c65927e720c3c12b39cd8ae144e90b`. `rdmapool`, `pvmpower`, `viostor`, `NetKVM`, `vioinput` and `DroidVM_Test.cer` are selected unchanged; virtio drivers derive from [virtio-win](https://github.com/virtio-win/kvm-guest-drivers-windows). `assets/windows/driver-files.json` records each file's source, licence and SHA-256. They are downloaded directly from upstream, not repacked into the GunyahVM input attachment. The public test certificate is not a production signing endorsement. |

The crosvm and firmware bytes were reused from the originating public project's 1.0.0 VM components; their full build recipes and hashes are retained here. Original neutral build paths and version labels embedded in these binaries are provenance; runtime paths use `gunyahvm` and explicitly override the crosvm pivot root. `NOTICE`, DEP-5 copyright, `assets/licenses/` and `assets/windows/licenses/` retain original attribution. The release input archive contains the first three table entries plus their component licences and NOTICE under `notices/`; all hashes and sizes are recorded in `tools/prebuilt.lock.json`.

## Rebuild crosvm

Prepare the exact compiler versions in `tools/crosvm.lock.json` and the Cargo source cache from the corresponding-source attachment. Each upstream archive, sysroot input, toolchain input and patch is pinned. Rebuilding needs substantially more disk space than package assembly.

```sh
python3 tools/fetch-crosvm-inputs.py --output inputs
python3 tools/build-crosvm.py --inputs inputs --cargo-cache cargo-cache --rust-toolchain /path/to/rust-1.88.0 --output build/crosvm
```

The build uses `--frozen --offline`, the locked Cargo.lock, SOURCE_DATE_EPOCH and neutral reference paths. The resulting ELF must match the locked hash. Original patch messages and collaborator credits are retained.

## Rebuild Windows components

```sh
python3 tools/fetch-windows-inputs.py --output windows-inputs
python3 tools/build-windows-assets.py --inputs windows-inputs --firmware-cache windows-inputs/firmware --output build/windows
python3 tools/build-virtualization-debs.py --crosvm-build build/crosvm --windows-build build/windows --output rebuilt-package
```

Firmware builds in a bubblewrap namespace. The small helper can also be rebuilt separately with `python3 tools/build-winpe-helper.py --tool-root EXTRACTED_LOCKED_LLVM --output winpe-helper.exe --work helper-work`. Signed drivers remain upstream binaries to preserve signatures. No Windows ISO, signing key or device is used by the build.

To recreate the exact release input archive with three binaries and their notices from locked build outputs, use GNU tar 1.35 and zstd 1.5.5:

```sh
python3 tools/bundle-prebuilt-inputs.py --crosvm build/crosvm/crosvm --firmware build/windows/assets/firmware.fd --winpe-helper build/windows/assets/winpe-helper.exe --output gunyahvm-1.0.0-inputs.tar.zst
```

The bundler verifies each binary and the resulting archive against the release lock; it does not upload anything.

## Corresponding source and verification

```sh
python3 tools/bundle-virtualization-source.py --inputs inputs --cargo-cache cargo-cache --output gunyahvm-1.0.0-source.tar.zst
python3 tools/lint.py
python3 -m pytest -q tools/tests
python3 -m pytest -q tools/tests --input-cache inputs/prebuilt
```

The source bundler accepts the pinned upstream source archives and Cargo registry source cache directly. It verifies archive hashes and crate checksums against Cargo.lock, includes maintained source, patches, text licences and build scripts, and excludes Git metadata, binaries, build products and logs. Firmware/driver source locations and fetch scripts are included; their permissively licensed source trees can additionally be included with `--windows-sources windows-inputs/firmware --driver-sources DRIVER_SOURCE_TREE`, checked against pinned commits. The GPL-covered crosvm and its dependency source are always included. Review the source manifest before distribution.

Both test commands run offline. The first runs all source-only tests, including mocked download/corruption and archive-boundary tests, and reports deselected payload integration tests. The second also runs real payload packaging, source-build-layout parity and compiled helper tests using the verified local cache. Use a clean Ubuntu 26.04 ARM64 rootfs under qemu-user to check dependency installation and integration syntax; hardware execution and seat changes require a Gunyah device.

## Runtime overlays

The package builder can consume a staged runtime root with `--runtime-root ROOT`; see [the manifest contract](runtime-components.md). It includes GTK, CLI, device profiles and default Windows assets. Tests cover capability/profile matching, backend arguments, fixed privileged inputs and lifecycle boundaries, including adapted iceland-vms cases. `tools/tests/smoke_gui.py OUTPUT` exercises the GTK pages and uses the upstream screenshot renderer when available.
