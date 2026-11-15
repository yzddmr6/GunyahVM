<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# macOS

Apple's macOS licence permits virtualization on Apple-branded hardware under its stated conditions. Qualcomm tablets are not Apple-branded hardware. Users must review the licence applicable to their software and make their own decision; GunyahVM does not grant additional rights. See [Apple's software licences](https://www.apple.com/legal/sla/) and [macOS 27 licence](https://www.apple.com.cn/legal/sla/docs/macOS27.pdf).

GunyahVM does not provide or redistribute Apple software. IPSWs, kernel collections, extracted components, SDKs, boot images and VM disks remain local user inputs. The macOS runtime package contains host-side crosvm, libvncserver and virglrenderer only. Firmware and guest graphics binaries are excluded from that package.

GunyahVM can import an already assembled native APFS bundle through New Virtual Machine → macOS. A complete official-download-to-first-boot creator is **not yet provided**. The upstream build scripts require additional SDK/toolchain preparation; importing a disk is not a replacement for installation.

## Existing installations

Upgrade the application and matching runtime packages together, with all VMs shut down. Do not uninstall the old version, recreate VMs or delete their directories. The runtime pre-install script preserves an already installed macOS firmware file locally under `/var/lib/gunyahvm/local-macos/firmware.fd` before dpkg removes the old package file. It never enumerates or rewrites the user's VM library. If a different preserved firmware already exists, upgrade stops instead of overwriting it.

The existing macOS, Ubuntu and Windows configurations remain in `~/.local/share/gunyahvm/vms/`. Their identifiers, disk contents and control identities are retained. For macOS, a VM-local `firmware.fd` takes precedence over the preserved firmware. GUI display reconnection uses the existing VMM; device compatibility still depends on the matching host and guest stack.

## Limitations

- The macOS boot geometry is fixed at assembly time: currently 1280×800. Window fitting changes display scale, not guest resolution. Changing RAM or vCPUs requires a matching boot bundle.
- The qualified 8 Pro combination uses 9 GiB / 6 vCPUs. OnePlus GPU reliability is still under investigation; no new OnePlus stability claim is made.
- Text clipboard transfer requires a logged-in desktop and the authenticated guest channel. Shared folders are unavailable for this profile. Native multi-touch, dynamic resolution and additional device support are not promised.
- Prototype inputs still disable fskitd. Repeated startup and GPU/pshare fault recovery remain acceptance items.
- New control identities can be generated locally by the assembly helper, but provisioning them into a new APFS guest is part of the unfinished creator. Import rejects a key already present in the selected library. Import into another library does not itself rotate guest keys. macOS cloning is disabled until guest-side rekeying is implemented. Existing instances are not automatically rekeyed.

The future creator must retrieve inputs only from Apple, resume interrupted downloads, retain verified content when cancelled, clean only its own staging directory, generate and install a distinct key per new VM, and publish a VM only after assembly succeeds. These are release requirements, not claims of current functionality.
