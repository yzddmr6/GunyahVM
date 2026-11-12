<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Linux guests

This guide takes you from choosing a Linux image to using graphics, SSH, shared folders and VM shutdown.

## Create your first VM

Use a normal account in an active local desktop session. If GunyahVM reports that the device can't run virtual machines yet, open **System Check** from the main menu and click **Load Drivers**. This loads VM modules already installed for the running host kernel. It cannot supply missing kernel support. The CLI equivalent is `gunyahvm load`.

Click **+** and choose **Ubuntu 26.04 LTS** to download the catalogue image, or **Linux Disk Image** for a local raw/qcow2 disk. A local image must contain an installed ARM64 Linux system, not an installer ISO; cloud-init is needed for the application's account and guest-control setup. A graphical window also needs a guest desktop. The Ubuntu cloud route installs a minimal desktop and enables guest autologin when a graphics mode is selected.

Review the name, processors, memory and disk size, choose graphics and click **Create**. Creation continues in the background; when the VM is ready, click **Start**. The CLI uses the same core:

```sh
gunyahvm load
gunyahvm create ubuntu --mirror official --start
gunyahvm list
```

For a local disk, add `--image /path/to/linux.qcow2`. The application copies the disk into its own VM storage. `--mirror tuna` and `--mirror ustc` choose alternative download mirrors; image checksums still come from Ubuntu's official build directory. `--release YYYYMMDD` selects a catalogue build. By default the application extracts boot files from the guest disk; `--boot-dir PATH` supplies a matching kernel/initrd directory for a local image.

## Connect to the guest

```sh
gunyahvm console ubuntu
gunyahvm ssh ubuntu
```

Cloud-init provisions the guest control channel and a password-locked SSH account. The application generates an SSH key unless you supply `--ssh-key`. Wait for `guest_ready` and `ipv4` in `gunyahvm list` before connecting. Imported images without the expected guest setup will not report readiness or accept these SSH credentials.

Ctrl-C detaches console log viewing. For `gunyahvm console ubuntu --interactive`, Ctrl-] detaches input. Reconnecting replays up to 256 KiB of console output.

## Select graphics

On the **Display** page of a stopped VM's **Settings**, choose **Basic Display** for software display or **OpenGL Acceleration** for virgl. The available choices reflect host and runtime capabilities. **OpenGL Acceleration** and **Standard 2D** (virtio-GPU 2D) automatically use the **GunyahVM Guest Kernel** with its matching modules. For example:

```sh
gunyahvm create desktop --guest-payload --gpu virgl --start
```

Start the VM in GunyahVM and select **Open Display** to use the embedded viewer or fullscreen. An imported system must provide its own desktop. Install `wl-clipboard` in the guest Wayland login session to transfer text on demand from the VM menu.

## Network and shared folders

**Internet via This Device** connects the guest through the host (NAT); **No Network** disables its virtual network. While the VM is stopped, add TCP/UDP rules under **Port Forwarding** on the **Network** page to expose a guest service on a host loopback port. Other local users can reach these forwarded services too, so retain authentication in the guest service. Optional vhost-net needs both host kernel support and device access.

Add a folder under **Sharing**, choose read-only or writable access and set the guest user and group. New Linux guests expose folders under `/mnt/gunyahvm/<tag>`. For existing guests, copy the mount command for that folder. Existing 9p configurations retain their backend. Guests can modify or delete files in a writable export.

## Shutdown, updates and data

```sh
gunyahvm stop ubuntu
gunyahvm restart ubuntu
```

`stop` requests a clean guest shutdown and waits for exit. **`stop --force` acts like cutting power and can lose data.** A failed guest shutdown is reported as a failure. Low battery, excessive temperature or host sleep also initiates shutdown; sleep may force termination after its deadline. Closing the manager only detaches it. Reopen the application to manage the running guest.

Stop all VMs before upgrading or removing the application. Release notifications open the download page; they do not install packages automatically. Disable checks in **Preferences** or launch with `GUNYAHVM_NO_UPDATE_CHECK=1`. Checks use a 24-hour cache and send no device information to GitHub.

VM data lives in `~/.local/share/gunyahvm/`, including disks under `vms/` and generated keys under `keys/`. Back up this directory with VMs stopped. Package removal preserves it. **`gunyahvm delete ubuntu` deletes the stopped VM and its disk**, so copy out needed data first.

With disk-managed boot files, the next start or guest-requested reset extracts kernel updates from the guest disk. With the **GunyahVM Guest Kernel** (Settings, **Advanced**), the kernel and modules instead come from the installed GunyahVM component package.

Thermal protection reads every zone with a passive trip from the running kernel's sysfs. Startup is refused when any monitored zone reaches its passive temperature, with a throttling message. During operation, reaching that zone's hot temperature requests a clean shutdown; when hot is absent, the threshold is critical minus 5°C. Forced termination occurs only after the existing shutdown deadline. Messages and state records identify the zone, reading and trip; battery protection is unchanged.
