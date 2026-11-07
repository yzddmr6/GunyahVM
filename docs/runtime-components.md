<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Runtime component interface

Runtime packages install `/usr/share/gunyahvm/runtime.json`. Schema `1` describes an `arm64` payload. Every component has a nonempty `version`, a `features` string array and a `files` object mapping roles to absolute installed paths below `/usr/lib/gunyahvm/` or `/usr/share/gunyahvm/`. Missing files, escaping paths and unknown schemas are rejected. The manifest describes build capabilities; it does not assert successful operation on a particular host.

| Component | Required file roles | Capability names / metadata |
| --- | --- | --- |
| `crosvm` | `binary` = `/usr/lib/gunyahvm/crosvm/crosvm` | `gunyah`, `net`, `vhost-net`, `simplefb`, `vnc`, `p9`, `gpu`, `runtime-share`, `shared-memory`, `balloon`, `input-modes`; `build_features` records the actual Cargo features |
| `renderer` | `library` | `virgl`, `venus`, `drm-msm`; the library directory is private to the VMM |
| `guest_kernel` | `image`, `initrd`; optional `modules` tar archive | `virtio-gpu`, `guest-accept`, `shared-boot`; exact `kernel_release`, optional `modules_load` list |
| `guest_mesa` | `archive` | `freedreno-virtio`; `guest_abi` such as `ubuntu:26.04`, relative `library_subdir` and `icd` |
| `uefi` | `image` | `windows-ecam` |
| `windows_drivers` | `inventory`, `certificate` | `test-signed`; inventory and driver/helper files share the Windows assets directory |

The guest initrd must contain the drivers required to mount the root disk and start networking. A modules archive contains paths relative to `/usr/lib/modules/<kernel_release>/`. The Mesa archive contains paths relative to `/usr/lib/gunyahvm/guest-mesa/`; its ICD references that final private prefix. The guest agent checks the guest OS/version before enabling native Mesa and removes its own loader/ICD configuration when leaving native mode. It never replaces the distribution's Mesa files.

Graphics are the intersection of the manifest, accessible DRM devices and the host Gunyah ABI. Venus/native require `GH_CAP_PROTECTED_SHARE`, crosvm runtime sharing and a matching GuestAccept guest. Native also requires an msm render node and matching guest Mesa. Shared memory is opt-in and excludes native graphics. A configuration must opt into the matching runtime guest kernel before selecting these modes. Plain virgl can remain available without protected share. GuestAccept runs in a single ordinary-user crosvm process; other modes retain crosvm's device sandbox. Balloon is disabled only when the installed build implements the corresponding option.

A runtime staging root can be supplied to `tools/build-virtualization-debs.py --runtime-root ROOT`. It must contain the manifest, each declared payload file, and `usr/share/doc/gunyahvm-runtime/` with provenance and licences. Every dependency file needed by a private library must be declared as an additional file role. Driver inventories also need each referenced driver/helper file declared. The builder overlays these components on the default crosvm/Windows bundle. It does not discover or enable graphics from an arbitrary directory of build outputs.
