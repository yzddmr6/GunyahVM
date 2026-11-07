<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Runtime component interface

Runtime packages install `/usr/share/gunyahvm/runtime.json`. Schema `1` describes an `arm64` payload. Every component has a nonempty `version`, a `features` string array and a `files` object mapping roles to absolute installed paths below `/usr/lib/gunyahvm/` or `/usr/share/gunyahvm/`. Missing files, escaping paths and unknown schemas are rejected. The manifest describes build capabilities; it does not assert successful operation on a particular host.

| Component | Required file roles | Capability names / metadata |
| --- | --- | --- |
| `crosvm` | `binary` = `/usr/lib/gunyahvm/crosvm/crosvm` | `gunyah`, `net`, `vhost-net`, `simplefb`, `vnc`, `p9`, `gpu`, `runtime-share`, `shared-memory`, `balloon`, `input-modes`; `build_features` records the actual Cargo features |
| `renderer` | `library`, `render_server` for Venus | `virgl`, `venus`, `drm-msm`; the library directory is private to the VMM |
| `guest_kernel` | `image`, `initrd`; optional `modules` tar archive | `virtio-gpu`, `guest-accept`, `shared-boot`; exact `kernel_release`, optional `modules_load` list |
| `guest_mesa` | `archive` | `freedreno-virtio`; `guest_abi` such as `ubuntu:26.04`, relative `library_subdir` and `icd` |
| `uefi` | `image` | `windows-ecam` |
| `windows_drivers` | `inventory`, `certificate` | `test-signed`; inventory and driver/helper files share the Windows assets directory |

The guest initrd must contain the drivers required to mount the root disk and start networking. A modules archive contains paths relative to `/usr/lib/modules/<kernel_release>/`. The Mesa archive contains paths relative to `/usr/lib/gunyahvm/guest-mesa/`; its ICD references that final private prefix. The guest agent checks the guest OS/version before enabling native Mesa. It installs an explicit GunyahVM native desktop session and `gunyahvm-graphics-run --native COMMAND`; these set private Mesa paths for their own processes. GNOME activation receives the same environment only for that login session. Leaving native mode removes that session choice. No distribution library, global loader configuration, global ICD or alternatives entry is changed.

Graphics are the intersection of the manifest, accessible DRM devices and the host Gunyah ABI. Venus/native require `GH_CAP_PROTECTED_SHARE`, crosvm runtime sharing and a matching GuestAccept guest. Native also requires an msm render node and matching guest Mesa. Shared memory is opt-in and excludes native graphics. A configuration must opt into the matching runtime guest kernel before selecting these modes. Plain virgl can remain available without protected share. Plain virgl explicitly disables GuestAccept. Venus/native run the GuestAccept devices in one ordinary-user crosvm process; Venus additionally starts the declared ordinary-user render server. Other modes retain crosvm's device sandbox. Balloon is disabled only when the installed build implements the corresponding option.

A runtime staging root can be supplied to `tools/build-virtualization-debs.py --runtime-root ROOT`. It must contain the manifest, each declared payload file, and `usr/share/doc/gunyahvm-runtime/` with provenance and licences. Every dependency file needed by a private library must be declared as an additional file role. Driver inventories also need each referenced driver/helper file declared. The builder overlays these components on the Windows bundle and splits the payload by component. Without this option it materializes the locked default graphics input using `tools/runtime_inputs.py`. It does not discover or enable graphics from an arbitrary directory of build outputs.

## Default package set

All four packages use the same exact Debian revision, `1.0.0-1`. `gunyahvm` depends on `gunyahvm-runtime (= 1.0.0-1)`, which depends on `gunyahvm-guest-kernel` and `gunyahvm-guest-mesa` at that same revision. The application owns the complete manifest, including Windows assets; the three payload packages own disjoint files. Their removal/upgrade guards refuse changes while a VM is running. Install all four local debs together.

The default payload is crosvm (`gunyah,net,vnc,virgl_renderer`), private virglrenderer 1.3.0 with MSM and Venus, Linux 7.2.15-gvm61 plus generic dracut initrd/modules, and Ubuntu 26.04 arm64 Mesa 26.2.5 with Freedreno virtio. Host libraries are resolved by package dependencies. The VMM/server RUNPATH uses `$ORIGIN`, and the application supplies the declared private server path. Host kernel packages and global renderer selection are unchanged.

`tools/runtime.lock.json` locks the input archive, every input member and each installed payload. `third_party/runtime/sources.json` fixes source commits/archives, patch order, build options, toolchains, target package hashes and the original versus relocated artifacts. This is the locked-input/locked-output assembly route, not a claim of byte-identical recompilation of every upstream project. `tools/build-runtime.py --input-cache CACHE --output ROOT` produces the staging contract offline; the package builder does this automatically. Corresponding source archives accompany binary distribution.
