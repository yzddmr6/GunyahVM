<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Runtime component interface

This guide defines the manifest and package layout needed to supply crosvm, graphics libraries and guest payloads to GunyahVM.

## Manifest schema

Runtime packages install `/usr/share/gunyahvm/runtime.json`. Schema `1` describes an `arm64` payload. Every component has a nonempty `version`, a `features` string array and a `files` object mapping roles to absolute installed paths below `/usr/lib/gunyahvm/` or `/usr/share/gunyahvm/`. Missing files, escaping paths and unknown schemas are rejected. The manifest describes build capabilities; it does not assert successful operation on a particular host.

| Component | Required file roles | Capability names / metadata |
| --- | --- | --- |
| `crosvm` | `binary` = `/usr/lib/gunyahvm/crosvm/crosvm` | `gunyah`, `net`, `vhost-net`, `simplefb`, `vnc`, `p9`, `gpu`, `runtime-share`, `shared-memory`, `balloon`, `input-modes`; `build_features` records the actual Cargo features |
| `renderer` | `library` | `virgl`; the library directory is private to the VMM |
| `guest_kernel` | `image`, `initrd`; optional `modules` tar archive | `virtio-gpu`, `guest-accept`, `shared-boot`; exact `kernel_release`, optional `modules_load` list |
| `guest_mesa` | `archive` | `freedreno-virtio`; `guest_abi` such as `ubuntu:26.04`, relative `library_subdir` and `icd` |

The guest initrd must contain the drivers required to mount the root disk and start networking. A modules archive contains paths relative to `/usr/lib/modules/<kernel_release>/`. The Mesa archive contains paths relative to `/usr/lib/gunyahvm/guest-mesa/`; its ICD references that final private prefix.

## Graphics requirements

Graphics are the intersection of the manifest, accessible DRM devices and the host Gunyah ABI. A configuration must opt into the matching runtime guest kernel before selecting virgl. Plain virgl can remain available without protected share. Plain virgl explicitly disables GuestAccept and retains the crosvm device sandbox.

## Supply a runtime staging tree

A runtime staging root can be supplied to `tools/build-virtualization-debs.py --runtime-root ROOT`. It must contain the manifest, each declared payload file, and `usr/share/doc/gunyahvm-runtime/` with provenance and licences. Every dependency file needed by a private library must be declared as an additional file role. The builder overlays these components and splits the payload by component. Without this option it materializes the locked default graphics input using `tools/runtime_inputs.py`. It does not discover or enable graphics from an arbitrary directory of build outputs.

## Default package set

All four packages use the same exact Debian revision, `1.0.0-1`. `gunyahvm` depends on `gunyahvm-runtime (= 1.0.0-1)`, which depends on `gunyahvm-guest-kernel` and `gunyahvm-guest-mesa` at that same revision. The application owns the complete manifest; the three payload packages own disjoint files. Their removal/upgrade guards refuse changes while a VM is running. Install all four local debs together.

The default payload is crosvm (`gunyah,net,vnc,virgl_renderer`), private virglrenderer 1.3.0, Linux 7.2.15-gvm62 plus generic dracut initrd/modules, and Ubuntu 26.04 arm64 Mesa 26.2.5 with Freedreno virtio. Host libraries are resolved by package dependencies. The VMM/server RUNPATH uses `$ORIGIN`, and the application supplies the declared private server path. Host kernel packages and global renderer selection are unchanged.

`tools/runtime.lock.json` locks the input archive, every input member and each installed payload. `third_party/runtime/sources.json` fixes source commits/archives, patch order, build options, toolchains, target package hashes and the original versus relocated artifacts. This is the locked-input/locked-output assembly route, not a claim of byte-identical recompilation of every upstream project. `tools/build-runtime.py --input-cache CACHE --output ROOT` produces the staging contract offline; the package builder does this automatically. Corresponding source archives accompany binary distribution.
