<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# Architecture

## Components

`bin/gunyahvm` imports the `gunyahvm` Python package. `cli.py` owns create/start/stop/list/plan/console/ssh/view/delete and supervision. `image.py` prepares Linux disks and boot files; `windows_vm.py` and `windows/` prepare and supervise Windows. The only virtualization backend is Gunyah. crosvm's internal upstream code is retained; the product exposes no alternative backend abstraction.

```mermaid
flowchart LR
    CLI[gunyahvm user command] --> PK[polkit authorization]
    PK --> LOAD[fixed module loader]
    PK --> NET[owned NetworkManager TAP]
    PK --> RUN[finite memlock then drop UID/GID]
    RUN --> SUP[ordinary-user supervisor]
    SUP --> VMM[crosvm with minijail]
    VMM --> DEV[/dev/gunyah]
    DEV --> RM[Gunyah resource manager]
```

## Discovery and resources

`host.py` reads the live device tree, including compatible strings, published VMIDs and 64-bit resource-manager capability IDs. It falls back to the kernel-owned live FDT when a distribution hides the `/hypervisor` sysfs directory. Missing VMIDs are reported as absent, never replaced by device constants. New guest VMIDs are allocated by the resource manager through the kernel, not chosen from a board table. The authoritative IDs used for hypercalls are those parsed by the kernel and returned to crosvm by ioctls.

Memory defaults use MemTotal, reserve at least 1 GiB or 25% for the host, and retain the tested 6 GiB memlock / 5120 MiB guest ceiling. MemAvailable is checked at start. Optional root-owned `/usr/share/gunyahvm/devices/<compatible>.conf` files may lower resource ceilings only. There are no shipped device overrides.

## Process and data lifetime

The privileged launcher sets bounded RLIMIT_MEMLOCK, initializes supplementary groups, drops all real/effective/saved GIDs and UIDs, then execs a fixed isolated Python entry point. It never reads user VM configuration while root. The supervisor and crosvm have distinct process groups; crosvm cleanup cannot kill its supervisor. State records boot ID and process start ticks to avoid confusing recycled PIDs. Each user's runtime flock permits one VM; other users remain a separate trust boundary.

VM data is private to its owner. TAP names and NetworkManager connection UUIDs derive from the VM UUID and caller identity. Guest control messages report readiness and acknowledge shutdown; extended crosvm exit status distinguishes clean stops, resets and failures. Windows installation completion requires a matching deployment token and clean shutdown.

## Pinned components

`tools/crosvm.lock.json` pins upstream, minijail, toolchain, Cargo input, patch order and ELF hash. `third_party/crosvm/patches/series` and each patch describe purpose, origin and upstream status. `tools/windows.lock.json` pins UEFI sources, build tools and signed drivers. `assets/` contains text notices and file inventories only. `tools/prebuilt.lock.json` pins the GunyahVM release input archive; signed drivers come directly from their locked upstream release. Downloads and cached inputs are verified before use, and `tools/assets.lock.json` checks the text notices.
