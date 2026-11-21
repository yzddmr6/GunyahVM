<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# macOS

GunyahVM creates macOS 27.2 virtual machines on the tablet itself: the application downloads only the parts it needs from Apple's servers, assembles them locally, and the first boot reaches the login window. macOS runs natively as ARM64 on Gunyah, and its graphics go to the Adreno GPU through a Metal plugin.

GunyahVM ships no Apple software; your device downloads every macOS file directly from Apple. Using macOS is subject to Apple's [software license agreement](https://www.apple.com/legal/sla/).

## Create

**In the application**: click **+ → macOS**, enter a VM name, a macOS account name and password, choose the disk size and click **Create**. A progress bar shows the download and setup; click **Pause** at any time and later **Resume** on the same entry to continue where it stopped. **Discard** removes the unfinished creation (downloaded files are kept, so the next attempt does not download them again).

**On the command line**:

```sh
gunyahvm create-macos macos            # asks for the macOS account password; the account defaults to your user name
gunyahvm start macos
gunyahvm view macos
```

- After an interruption (Ctrl-C, a lost network, a shutdown) run the same command again to resume.
- `--account NAME` sets the account name, `--password-fd N` reads the password from a file descriptor (for scripts; never put the password on the command line), `--disk-gib N` sets the disk size (40–512, default 64).
- `--keep-downloads` keeps Apple's files so another macOS VM can be created without downloading; `--discard` removes this VM's unfinished creation.
- `gunyahvm fetch-macos-inputs` only downloads, so you can fetch the files where the network is good.

**What it needs**: about 13.3 GB of downloads; at most about 27 GB of free space while creating, and about 22 GB for the finished VM (the disk grows as it is used). The VM uses 9 GB of memory and 6 processors; it starts when that much memory is available beside the VMs already running. You can keep using the tablet and other VMs while it is created; in the last few minutes, the helper VM that writes the disk needs 2 GB of free memory.

## What creation does

1. **Download**: the 7 files used from two macOS 27.2 restore images (the system volume, two cryptexes, the kernel collection and three trust caches) are fetched member by member from Apple's CDN; each is checked against its ZIP checksum and a pinned SHA-256. Downloads resume, and only HTTPS redirects within Apple's domains are followed.
2. **Decrypt**: the system images are Apple Encrypted Archives; the key comes from Apple's public key server, every segment is authenticated, and the result is written straight into the new disk.
3. **Link the kernel**: GunyahVM's XNU kernel and board drivers are linked into Apple's kernel collection; the result is byte-identical to the build qualified before release. The trust cache is Apple's three plus the Metal plugin.
4. **Write the disk**: an APFS container with System, Data, Preboot, VM, Update, Hardware and xART volumes. A temporary Linux helper VM (the packaged guest kernel and APFS driver) writes the files, so the host kernel needs no APFS support and no administrator password is asked; the helper powers off when done.
5. **Account and driver**: your administrator account is added (only a salted hash is kept, also while paused), the first-run assistant is skipped, the Metal plugin is installed, and a new control key is generated for this VM with its public half placed in the guest. Every VM has its own key, kept only on this device.
6. **Boot disk**: the board's Apple device tree and the EFI boot disk are generated.
7. **Publish**: only when everything is done does the VM appear in the library, in one step; an unfinished creation lives in `~/.local/share/gunyahvm/.creating/` and never shows up half-built.

## Use

Start the VM, open its display in the application and sign in with the account from creation. While the display has keyboard focus, shortcuts such as Alt+F4 and the Super key go to the VM; the first time, GNOME asks whether to allow this, and Super+Esc gives the shortcuts back to the desktop. Use **Shut Down** (it asks the guest to shut down over the control channel) rather than a forced stop. Text clipboard works once you are signed in, from the VM menu.

## Specifications and limits

| Item | Status |
| --- | --- |
| Memory / processors | Fixed at 9 GB / 6 |
| Resolution | Fixed at 1280×800; fitting the window scales |
| Disk | 40–512 GB, chosen at creation |
| Graphics | Metal (Adreno, through Turnip) |
| Network | NAT |
| Clipboard | Text, after signing in |
| Shared folders, sound, USB | None |
| Apple ID, iCloud, App Store and other services that need Apple hardware identity | Not available |
| Clone | Not available (cloning would need a guest-side key change) |

Supported devices: Xiaomi Pad 8 Pro (system 1.3.1 or later) and OnePlus Pad 2 Pro (v0.9.1 or later). On the OnePlus Pad 2 Pro, macOS can hang under heavy graphics load; force-stop and start the VM again.

GunyahVM checks what macOS needs from the running kernel, not its version: Gunyah VM-fd memory sharing and, for graphics, an accessible Adreno render node and `/dev/udmabuf`, so a kernel you build yourself can run macOS too. Heavy macOS graphics also needs two msm driver fixes in the host kernel, which the application cannot detect: “drm/msm: allow virtually contiguous submit bookkeeping” (upstream Linux has an equivalent change) and “drm/msm: honor write-back system RAM on coherent GPU imports”.

## The boot kit

`/usr/lib/gunyahvm/macos/kit/` in the package is GunyahVM's own part; `kit.json` lists the hash of every file:

- XNU kernel: Apple's open-source xnu-12377.121.6 with the Droid-VM/macOS-DroidVM patches (large-memory layout, 4 KiB page tables, Gunyah board support).
- Board drivers: object files of 8 kexts (virtio block, data disk, AES, display over virtio-gpu, network, input, Adreno GPU), linked on the device.
- Metal plugin: Turnip (Mesa) with the metal2vulkan bridge.
- UniXNU and DarwinBoot (EFI loaders), EDK2 firmware, and the board's device tree.
- For the helper VM: the linux-apfs-rw module and GNU tar, plus mkapfs (apfsprogs).

Sources and build steps are in [NOTICE](../NOTICE) and [Building](building.md).

## Existing VMs

Upgrading from the private macOS packages or from an imported boot bundle keeps the VM's disk, key and configuration as they are. Firmware comes from the VM directory's `firmware.fd` first, then the boot kit, then the copy an earlier upgrade kept in `/var/lib/gunyahvm/local-macos/firmware.fd`. Boot bundles assembled earlier can still be imported under **+ → macOS → Advanced**, or with `gunyahvm import-macos NAME BUNDLE`.

## Troubleshooting

- **Slow or interrupted download**: pause, change networks and resume; downloaded parts are not fetched again.
- **Not enough space**: free space is checked before creating; free some and click **Resume**.
- **"Stop your running virtual machine before creating macOS"**: shut down the running VM first.
- **The helper VM failed**: the error shows the helper's last log lines; the full log is in `~/.local/share/gunyahvm/.creating/NAME/helper-*.log`. **Resume** retries directly; if it still fails, **Discard** and create again.
- **Forgot the macOS password**: for now, delete the VM and create it again.
