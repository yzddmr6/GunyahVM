<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Building GunyahVM

This guide explains how to assemble the Debian packages from pinned inputs, check the source tree and prepare corresponding source for distribution.

## Assemble the release packages

On Linux, install Python 3.11 or newer, GNU tar, zstd and `dpkg-deb`. Package assembly uses prebuilt ARM64 inputs; it does not compile crosvm, firmware, the guest kernel or graphics libraries.

```sh
python3 tools/fetch-prebuilt-inputs.py --cache inputs/prebuilt
python3 tools/build-virtualization-debs.py --input-cache inputs/prebuilt --offline --output dist
```

The fetch step downloads application inputs, the graphics runtime, upstream signed drivers and the ARM64 OpenSSH offline package. Their URLs and hashes are pinned in `tools/prebuilt.lock.json`, `tools/runtime.lock.json` and `tools/windows.lock.json`. You can also populate the cache with those exact archives yourself. The offline build fails if any required input is missing or has the wrong checksum. Without `--offline`, the builder downloads missing inputs.

The output contains four `dist/gunyahvm*_1.2.0-1_arm64.deb` packages, staging trees, a file manifest and `SHA256SUMS`. On a supported ARM64 host, install all four together:

```sh
sudo apt install ./dist/gunyahvm*_arm64.deb ./gir1.2-casilda-1.0_*_arm64.deb ./libcasilda-1.0_*_arm64.deb
```

The embedded viewer also needs the Casilda runtime library and GI package built using `tools/build-casilda.py`, plus wlroots 0.20. Install these in the same transaction or use a system image that already includes them.

Use `dpkg-deb --info PACKAGE.deb` and `dpkg-deb --contents PACKAGE.deb` to inspect a package without installing it. The [runtime component interface](runtime-components.md) defines the package split and the optional `--runtime-root ROOT` staging layout.

## Source inputs and rebuilds

The repository stores application source, patches, licenses and documentation images. Executable payloads are fetched as pinned build inputs. Each lock file records the hashes used for package assembly; `third_party/runtime/sources.json` records graphics source archives, patches, toolchains and build options. [NOTICE](../NOTICE) records component origins and attribution.

To rebuild baseline crosvm, prepare the compiler versions specified in `tools/crosvm.lock.json` and a Cargo source cache from the corresponding-source archive:

```sh
python3 tools/fetch-crosvm-inputs.py --output inputs
python3 tools/build-crosvm.py --inputs inputs --cargo-cache cargo-cache --rust-toolchain /path/to/rust-1.88.0 --output build/crosvm
```

This route uses the pinned crosvm/minijail sources, ordered patch queue and Cargo lock, with `--frozen --offline`. It checks the resulting ELF against the locked hash. The baseline has `gunyah,net,vnc` enabled; default package assembly overlays the graphics variant, which also enables `virgl_renderer`. The graphics variant's recipe and artifact hashes are recorded separately in `third_party/runtime/sources.json` and `tools/runtime.lock.json`.

`tools/build-runtime.py --input-cache inputs/prebuilt --output build/runtime` assembles the default runtime staging tree from the locked binary archive. It does not compile the upstream projects. The guest initrd recipe is `tools/build-guest-initrd.sh IMAGE OUTPUT`, run inside the pinned Ubuntu ARM64 environment with matching modules installed. Runtime assembly checks fixed input and output hashes; it does not promise byte-identical upstream recompilation.

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
python3 tools/bundle-virtualization-source.py --inputs inputs --cargo-cache cargo-cache --runtime-sources inputs/runtime-sources --output gunyahvm-1.2.0-source.tar.zst
```

Supply pinned upstream archives and the Cargo registry source cache. The bundler verifies archive hashes and crate checksums, includes maintained source, patches, licenses and build scripts, and excludes executable payloads, Git metadata, build products and logs. Source archives for guest Linux, virglrenderer and Mesa are mandatory and checked against `third_party/runtime/sources.json`.

Review the generated source manifest and distribute the corresponding-source archive with the binary packages. Preserve upstream copyright and license files. The build tools and CI produce artifacts; they do not publish a release.

## Windows installation payload

`tools/windows.lock.json` pins the ARM64 OpenSSH ZIP version, source and SHA256. The offline cache must also contain `OpenSSH-ARM64.zip`. Package assembly and installation-media preparation verify it and retain upstream licences. Guest first configuration installs the service locally without fetching Windows Update features. Put `pwsh` on `PATH` to run the PowerShell script tests. Actual Windows installation and control still require device validation.
