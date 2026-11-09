<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# Building GunyahVM

## Assemble the packages

Use Python 3.11 or newer, GNU tar, zstd and `dpkg-deb` on Linux. The Git repository contains source and text only. The package builder fetches `gunyahvm-1.0.0-inputs.tar.zst` from [GunyahVM v1.0.0](https://github.com/yzddmr6/GunyahVM/releases/tag/v1.0.0), using the URL and SHA-256 in `tools/prebuilt.lock.json`. It fetches the original signed driver ZIP directly from Droid-VM using `tools/windows.lock.json`. The default graphics archive named in `tools/runtime.lock.json` must also be in the cache. No crosvm, renderer, kernel or firmware compilation occurs during package assembly; all input and final runtime payload hashes are fixed.

```sh
python3 tools/fetch-prebuilt-inputs.py --cache inputs/prebuilt
python3 tools/build-virtualization-debs.py --input-cache inputs/prebuilt --offline --output dist
```

Omitting the separate fetch step and `--offline` lets the builder download missing inputs itself. Cached files are checked on every invocation. A checksum mismatch fails the build; it never silently replaces a bad cache entry. Downloaded files enter the cache only after verification. The release archive must contain exactly its locked binary and notice files, with matching sizes and hashes; links, duplicate members and unexpected paths are rejected. Only the selected, individually hashed driver files are extracted from the upstream ZIP. Text licences and inventories are also checked.

The output is four `dist/gunyahvm*_1.0.0-1_arm64.deb` packages, staged trees, a per-package file manifest and SHA256SUMS. Install all four together: `sudo apt install ./dist/gunyahvm*_arm64.deb`. Exact package dependencies are described in [the runtime contract](runtime-components.md). Inspect it with `dpkg-deb --info` and `dpkg-deb --contents`.

Before the first release exists, place both locally prepared input attachments and the locked `gunyah-arm64-drivers.zip` in the cache and use `--offline`. The same hash checks apply; no GitHub release is created by these tools. After approval, publish the exact input attachment alongside the Debian package and corresponding source. The package CI job downloads that attachment and will require its publication; lint and source-only offline tests work before publication. CI uploads build artifacts but does not publish releases.

## Binary origins

| Payload | Source and build |
| --- | --- |
| `crosvm` | ChromiumOS crosvm `96e84c3f680e34e82e932ef911ae97d734121758`, minijail `7845d89927a82dbdcdee6276837cd0978f69ba19`; iceland-gvm `7ae498c24e61a8860740d003572455a7443eecd3` patches 0001–0030, plus maintained patches 0031 (quiet closed shutdown channel) and 0032 (bounded child teardown). The full ordered queue, purpose, origin and upstream status are in `third_party/crosvm/patches/series` and each patch. Built with Rust/Cargo 1.88.0, GCC 13.3.0 ARM64 cross compiler, binutils 2.42, LLVM/libclang 18.1.3 and the Ubuntu 26.04 ARM64 sysroot locked in `tools/crosvm.lock.json`; base build features `gunyah,net,vnc`, default features disabled. The shipped graphics variant adds `virgl_renderer`; its artifact and recipe are locked separately by `tools/runtime.lock.json` and `third_party/runtime/sources.json`. |

The baseline crosvm and firmware bytes were reused from the originating public project's 1.0.0 VM components; their full build recipes and hashes are retained here. Original neutral build paths and version labels embedded in these binaries are provenance; runtime paths use `gunyahvm` and explicitly override the crosvm pivot root. `NOTICE`, DEP-5 copyright, `assets/licenses/` and `assets/windows/licenses/` retain original attribution. The release input archive contains the locked binaries plus their component licences and NOTICE under `notices/`; all hashes and sizes are recorded in `tools/prebuilt.lock.json`.

## Rebuild the baseline crosvm

The baseline route supplies the baseline binary; the package builder overlays the default graphics variant. Prepare the exact compiler versions in `tools/crosvm.lock.json` and the Cargo source cache from the corresponding-source attachment. Each upstream archive, sysroot input, toolchain input and patch is pinned. Rebuilding needs substantially more disk space than package assembly.

```sh
python3 tools/fetch-crosvm-inputs.py --output inputs
python3 tools/build-crosvm.py --inputs inputs --cargo-cache cargo-cache --rust-toolchain /path/to/rust-1.88.0 --output build/crosvm
```

The build uses `--frozen --offline`, the locked Cargo.lock, SOURCE_DATE_EPOCH and neutral reference paths. The resulting ELF must match the locked hash. Original patch messages and collaborator credits are retained.

## Corresponding source and verification

```sh
python3 tools/bundle-virtualization-source.py --inputs inputs --cargo-cache cargo-cache --runtime-sources inputs/runtime-sources --output gunyahvm-1.0.0-source.tar.zst
python3 tools/lint.py
python3 -m pytest -q tools/tests
python3 -m pytest -q tools/tests --input-cache inputs/prebuilt
```

The source bundler accepts the pinned upstream source archives and Cargo registry source cache directly. It verifies archive hashes and crate checksums against Cargo.lock, includes maintained source, patches, text licences and build scripts, and excludes Git metadata, binaries, build products and logs. The GPL-covered crosvm and its dependency source are always included. Guest Linux, patched virglrenderer and Mesa source archives from `--runtime-sources` are also mandatory and checked against `third_party/runtime/sources.json`. Review the source manifest before distribution.

Both test commands run offline. The first runs all source-only tests, including mocked download/corruption and archive-boundary tests, and reports deselected payload integration tests. The second also runs real payload packaging, source-build-layout parity and compiled helper tests using the verified local cache. Use a clean Ubuntu 26.04 ARM64 rootfs under qemu-user to check dependency installation and integration syntax; hardware execution and seat changes require a Gunyah device.

## Runtime overlays

The package builder can consume a staged runtime root with `--runtime-root ROOT`; see [the manifest contract](runtime-components.md). It includes GTK, CLI and device profiles. Tests cover capability/profile matching, backend arguments, fixed privileged inputs and lifecycle boundaries, including adapted iceland-vms cases. `tools/tests/smoke_gui.py OUTPUT` exercises the GTK pages and uses the upstream screenshot renderer when available.

## Locked graphics inputs

`tools/runtime.lock.json` pins `gunyahvm-runtime-1.0.0-inputs.tar.zst`, every regular member and every installed runtime payload. `tools/runtime_inputs.py` verifies and normalizes module/Mesa archive paths, writes private-prefix ICDs and emits the runtime contract. The original crosvm and render-server RUNPATH were relocated with the recorded patchelf version; both original and relocated hashes are retained. The library is not installed in the system loader search path.

`third_party/runtime/sources.json` retains exact upstream identities, kernel and renderer patch queues, compiler/tool hashes, build options, sysroot package versions/URLs/hashes and source-archive hashes. The guest queue contains the actual Linux 7.2 adaptations, including execbuffer synchronization and its configuration fragment. Mesa uses the fixed official release archive rather than an unpinned Git branch. This assembly route accepts only the locked binary input, not arbitrary files from another build tree. It does not claim upstream recompilation is bit reproducible.

The kernel payload uses the exact kernel/modules pair plus a generic Ubuntu dracut initrd. `tools/build-guest-initrd.sh IMAGE OUTPUT` is its recipe, run in the locked Ubuntu arm64 environment with modules installed at the standard path. It excludes host library configuration and account records. The installed versions of its userspace build root accompany the payload. `tools/build-runtime.py --input-cache inputs/prebuilt --output build/runtime` materializes the release contract separately when needed; ordinary package assembly calls it automatically.

## Windows offline control setup

`tools/windows.lock.json` also pins the upstream [Win32-OpenSSH ARM64 10.4.0.0p1-Preview](https://github.com/PowerShell/Win32-OpenSSH/releases/tag/10.4.0.0p1-Preview) ZIP. Both input fetchers include it; copy `OpenSSH-ARM64.zip` into the input cache before an offline build. The existing baseline release archives remain unchanged. Fresh Windows asset builds and prebuilt package assembly use the same ZIP and retain its original licence notices.

Package assembly verifies the ZIP against the source lock and writes `openssh.json`. Media preparation checks it before processing the ISO, copies the ZIP and manifest into the payload embedded in WinPE and the driver disk, and checks the copied bytes again. WinPE skips network initialization and copies the payload to `C:\gunyahvm`. First configuration verifies SHA256 locally, installs through the bundled upstream service installer, generates host keys, checks `sshd_config` and starts sshd with key-only authentication and gateway-scoped firewall access. Missing or damaged inputs fail explicitly without a download fallback. The service installer has a 90-second timeout.

For a fully offline account-creation path, supply `--local-user vmuser` when creating the VM (the terminal prompts for the password), or use `--password-fd` for automation. Without an explicit local account, the ISO's normal OOBE remains in charge of account creation and the agent runs after first login; its Microsoft-account flow may require Internet access.

The media offers a read-only check that needs neither administrator rights nor Internet access:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File C:\gunyahvm\install-openssh.ps1 -CheckOnly
```

Success prints `GUNYAHVM_OPENSSH_VERIFIED` with the version and SHA256. Installation failures are recorded in `C:\gunyahvm\openssh-install*.log`; first-boot failures also appear in `first-boot.log`. Recreate media made with older packages; installing a new host package does not change existing guest disks or prepared media.

Offline tests include a real FAT media roundtrip, ARM64 PE checks, damaged/missing inputs and the executable PowerShell preflight. Add `pwsh` to `PATH` and install `fdisk`, `dosfstools` and `mtools` to run every case with `--input-cache`. A successful host test does not validate Windows service startup or guest SSH; release acceptance also requires an offline first boot and authenticated control on the target device.
