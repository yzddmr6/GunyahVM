<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# GunyahVM

**Turn a Snapdragon tablet running mainline Ubuntu into a portable VM workstation: use Gunyah hardware virtualization to run ARM64 Linux, Windows and locally assembled macOS, open their desktops and manage their lifecycle in one application.**

[简体中文](README.md) · [Download](https://github.com/yzddmr6/GunyahVM/releases/latest) · [Issues](https://github.com/yzddmr6/GunyahVM/issues)

![Ubuntu desktop in GunyahVM](docs/images/manager.png)

## Capabilities

- **Multiple desktop systems on a tablet:** download and configure Ubuntu automatically, install Windows offline from your ARM64 ISO, or import a locally assembled native APFS macOS boot bundle.
- **Embedded display:** view, scale, fullscreen and reopen the guest desktop in a GTK window. Closing the manager does not shut down the independently supervised VM.
- **Guest-specific graphics:** Linux supports virgl OpenGL acceleration; a matching local macOS guest can use the Turnip/Metal graphics path. Windows uses a software framebuffer and has no 3D acceleration.
- **Daily management:** GUI and CLI share configurations, NAT, SSH control, port forwarding and stopped-disk snapshots. Linux supports shared folders and explicit text clipboard transfers; macOS clipboard requires a logged-in desktop.

| Guest | Creation | Display and integration |
| --- | --- | --- |
| Ubuntu / Linux | Automatic Ubuntu download, or an installed ARM64 raw/qcow2 disk | Embedded display, virgl, text clipboard, virtiofs/9p sharing |
| Windows | Your Windows 11 ARM64 ISO, deployed offline | Fixed-resolution software display, key-only SSH; no shared folders, clipboard or 3D |
| macOS | Your assembled native APFS bundle; automatic creation from scratch is not available | Fixed 1280×800, matching guest GPU stack, text clipboard; no shared folders |

A specific macOS graphics workload combination has been validated on Xiaomi Pad 8 Pro. OnePlus GPU reliability has known issues; results on one device do not establish support on every device.

## How it works

**Interface and core.** The GTK/libadwaita application and CLI use one core and one user library, with common configuration, state, shutdown and snapshot behavior.

**VM and display.** crosvm runs ARM64 guests through the kernel's Gunyah interface. Casilda receives their local Wayland display. Linux virgl and the dedicated macOS graphics path require their respective runtime components; they do not automatically accelerate Windows.

**Host integration and data.** A small privileged helper manages permissions and networking; VMs run as the ordinary user. Application/runtime updates are separate from user disks, images and keys. See [Architecture](docs/architecture.md) and the [security model](docs/security.md).

## Download and install

You need ARM64 mainline Ubuntu, a Gunyah-capable device kernel, a Wayland desktop and the declared GTK/Casilda dependencies. Use the latest supported device system, such as [Xiaomi Pad 8 Pro mainline Ubuntu](https://github.com/yzddmr6/xiaomipad-8pro-mainline/releases/latest). Installing the application cannot add missing kernel virtualization support.

1. Download all four `gunyahvm*_arm64.deb` packages from the same [release](https://github.com/yzddmr6/GunyahVM/releases/latest) into a directory containing only that release's packages. Follow its instructions for extra dependencies such as Casilda.
2. In that directory run:

   ```sh
   sudo apt install ./gunyahvm*_arm64.deb
   ```

3. Open GunyahVM as your normal desktop user. **System Check** reports driver, permission, memory and runtime requirements. **Load Drivers** only loads modules already installed for the running kernel.

## Create and use guests

### Ubuntu / Linux

Choose **+ → Ubuntu 26.04 LTS → Create**, selecting graphics for a desktop. Then choose **Start** and **Open Display**. **Linux Disk Image** accepts an installed ARM64 raw/qcow2 system, not an installer ISO; automatic account configuration requires cloud-init.

```sh
gunyahvm load
gunyahvm create ubuntu --mirror official --guest-payload --gpu virgl --start
gunyahvm list
gunyahvm ssh ubuntu
gunyahvm stop ubuntu
```

Add read-only or writable shares in settings while stopped; guests access `/mnt/gunyahvm/<tag>`. Text clipboard requires `wl-clipboard` in the logged-in guest Wayland session and explicit transfers from the VM menu. More options: [Linux guide](docs/linux.md).

### Windows

Provide a valid Windows 11 **ARM64** ISO and licence; the project supplies neither Windows images nor activation. Choose **+ → Windows 11**, select the ISO, enter a local account and password, review resources and create. Start for automatic deployment, then open the display to complete region, keyboard, licence and privacy choices.

```sh
gunyahvm create windows --os windows11 --iso /path/to/windows-arm64.iso --local-user vmuser
gunyahvm start windows
gunyahvm view windows
```

The CLI prompts for the password without echo; automation uses `--password-fd`, never a command-line password. At least 4608 MiB RAM and a 64 GiB logical disk are required; disk allocation grows on demand. Installation needs no Internet, but default NAT permits external access. Clean shutdown needs the guest NIC and SSH control service. See [Windows guide](docs/windows.md).

### macOS: licence, official inputs and import

**Apple's software licence permits macOS virtualization only on Apple-branded hardware under its terms. Qualcomm tablets are not Apple hardware. Users must review the applicable licence and make their own decision; this project grants no additional rights.** [Apple software licences](https://www.apple.com/legal/sla/). The project does not provide or redistribute Apple software or preinstalled disks.

The available entry point imports a locally assembled boot bundle. **There is no executable automatic creation command from scratch.** The following input workflow is not a completed one-click installer:

1. Obtain the matching system IPSW and required build SDKs through Apple's official channels and read their licences. Do not use third-party preinstalled disks, extracted kernels or shared control keys.
   Fetch the two pinned official IPSWs (about 46.5 GB total, plus SDK and expansion space):

   ```sh
   gunyahvm fetch-macos-inputs "$HOME/.cache/gunyahvm/macos-official"
   ```

   Cancel with Ctrl-C; rerun the same command to resume. Downloads follow only HTTPS redirects to Apple domains and become valid inputs only after full SHA256 verification. Remove only unfinished files while keeping verified inputs with:

   ```sh
   gunyahvm fetch-macos-inputs "$HOME/.cache/gunyahvm/macos-official" --clean-partials
   ```

2. Assemble native APFS System/Data volumes and a matching boot bundle locally, generating a control key for the new VM and installing its public key inside that guest. Community assembly currently needs additional toolchain preparation; GunyahVM does not yet provide a complete creator.
3. With a matching bundle, choose **+ → macOS → Local Boot Bundle → Import**, then start. Its `inputs.json` verifies boot files, disk and control key. Firmware remains a local VM input or the local file preserved by an upgrade.

CLI import and startup are also available:

```sh
gunyahvm import-macos macos /path/to/local-bundle
gunyahvm start macos
gunyahvm view macos
```

Portable bundles use schema 3 with `firmware.fd` in the hash inventory. Legacy schema 2 continues to use locally preserved firmware.

Resolution is fixed; RAM or CPU changes require reassembly of matching boot inputs. Cloning is unavailable until guest-side rekeying is implemented. Prototype fskitd and fault-recovery limitations remain. See [macOS requirements and limitations](docs/macos.md).

## Upgrade and preserve data

Shut down all VMs normally, download the four matching packages and run the installation command above. **Do not uninstall first, delete or recreate existing VMs.** Data remains under `~/.local/share/gunyahvm/vms/`; stop before backing up and include configurations and keys.

When upgrading the former private macOS package, pre-installation preserves its installed firmware at `/var/lib/gunyahvm/local-macos/firmware.fd` before removal of the old package file, without changing user libraries. A different existing preserved firmware stops the upgrade instead of being overwritten. Existing macOS, Ubuntu and Windows disks and control identities are retained. Update notifications provide download links, not automatic installation.

## Frequently asked questions

- **Cannot start?** Open System Check or run `gunyahvm check --json`. Check the kernel, Wayland session, memory and dependencies; do not run the GUI as root.
- **Resizing the window does not change guest resolution?** Window fitting scales the display. Windows/macOS boot geometry is fixed; dynamic resolution is not implemented.
- **VM remains running after closing the GUI?** This is expected. Reopen its display, or choose **Shut Down** to stop it. Force Off is equivalent to cutting power and may lose data.
- **Sharing or clipboard unavailable?** Consult the guest table. Windows has neither; macOS has no shared folders; Linux clipboard needs a logged-in Wayland session and guest tools.
- **How many VMs at once?** Currently one per user. Audio, USB passthrough, live migration, guest Secure Boot and vTPM are unavailable.
- **Report an error?** Include device, system/application versions, error and reproduction steps in [Issues](https://github.com/yzddmr6/GunyahVM/issues). Remove personal information and guest data; never upload Apple files or keys. Use [security reporting](SECURITY.md) for vulnerabilities.

Guests can modify or delete writable shared files. Explicitly enabled legacy CLI VNC is accessible to other local accounts. NAT is not Internet isolation.

## Development and acknowledgements

[Building](docs/building.md) · [Contributing](CONTRIBUTING.md) · [Device profiles](docs/new-device.md). Thanks to iceland-vms/iceland-gvm, DroidVM, crosvm, minijail, Casilda, virglrenderer, Linux/Mesa, GTK/libadwaita, librevm, edk2 and virtio-win. Exact attribution is in [NOTICE](NOTICE).

GunyahVM uses [GPL-2.0-only](LICENSE); third-party components retain their [licences](debian/copyright).
