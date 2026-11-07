<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# Windows user guide

## Inputs

Windows is included in `gunyahvm`; Remmina, its VNC plugin, wimtools, 7zip, ntfs-3g, python3-hivex, dosfstools and mtools are mandatory dependencies. Supply your own official Windows 11 ARM64 ISO containing `sources/install.wim` and a suitable licence. No Microsoft media or preset account is distributed. Keep 40 GiB free on ext4 or XFS for preparation and a growing 64 GiB sparse disk.

## Install and display

```sh
gunyahvm load
gunyahvm create windows --os windows11 --iso ~/Downloads/Windows11_ARM64.iso --start
gunyahvm view windows
```

Choose an edition from the WIM, or pass its exact name with `--edition 'Windows 11 Pro'`. Defaults are up to four online vCPUs, 4608 MiB RAM and a 256 MiB DMA pool. The guest memory ceiling is at most 5120 MiB and may be lower on smaller hosts. Creation refuses a host without the minimum budget.

Media preparation is unprivileged and uses no mounts. WinPE applies Windows with DISM and installs the bundled drivers. A successful deployment followed by clean shutdown detaches and deletes installation media, then boots the system disk into ordinary OOBE. Failed deployments keep media and logs. A force stop never marks installation complete. Complete OOBE in Remmina; if the connection drops during a Windows-requested reset, run `view` again.

VNC listens only on `127.0.0.1`; `--vnc-port 5901` selects another unprivileged port. There is no VNC password, so other local accounts can connect. Do not expose the port over a network. The 1280×720 framebuffer is software rendered.

## Accounts and shutdown

No local account, automatic login or RDP is enabled by default. To create an account explicitly, use `--local-user vmuser` and enter its password interactively. Windows hidden-password answer-file encoding is not encryption. Preparation files are owner-only, host installation media is deleted after deployment, and guest answer files are deleted after first login. Enable RDP yourself in a supporting Windows edition and verify its server certificate.

Shut down from Windows' Start menu, then:

```sh
gunyahvm stop windows
gunyahvm start windows
gunyahvm view windows
```

Normal `stop` requires shutdown inside Windows while it is running. `stop --force` is abrupt and may lose data. Keep the host awake, and shut down the guest before host shutdown. `delete windows` permanently removes its disk and logs after stopping. Purging the package preserves VM data.

## Limits

No vTPM, guest Secure Boot, GPU acceleration, audio or USB passthrough. TPM and related setup checks are bypassed; test-signed drivers require Test Mode and its watermark. Hibernation and Fast Startup are disabled. The protected Gunyah execution mode does not make the guest's installation or drivers secure-boot verified. See the component licences and [security model](security.md).
