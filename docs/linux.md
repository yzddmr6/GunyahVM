<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Linux guests

This guide takes you from choosing a Linux image to using graphics, SSH, shared folders and VM shutdown.

## Create your first VM

Use a normal account in an active local desktop session. Open **Host information** with the monitor icon in the application header, then click **Load drivers**. This loads VM modules already installed for the running host kernel. It cannot supply missing kernel support. The CLI equivalent is `gunyahvm load`.

Click **+** and choose **Ubuntu cloud image** to download a catalogue image, or **Linux ARM64 disk image** for a local raw/qcow2 disk. A local image must contain an installed ARM64 Linux system, not an installer ISO; cloud-init is needed for the application's account and guest-control setup. A graphical window also needs a guest desktop. The Ubuntu cloud route installs a minimal desktop and enables guest autologin when a graphics mode is selected.

Choose CPU, memory and disk size, select graphics, click **Create**, then select the VM and click **Start**. The CLI uses the same core:

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

In a stopped VM's **Settings**, choose **Software** for software display or **OpenGL (virgl)** for GL acceleration. The available choices reflect host and runtime capabilities. For virgl or virtio-GPU 2D, enable **Runtime guest kernel** to use the matching kernel and modules. For example:

```sh
gunyahvm create desktop --guest-payload --gpu virgl --start
```

Local windows require a Wayland session. Imported disks must provide a desktop compatible with the selected display mode; changing the graphics setting alone does not install one.

To use VNC instead of a local window, enable **VNC** in Settings and connect a viewer to `127.0.0.1` at the configured port (default `5900`). **VNC has no authentication: other local accounts can view and control the guest.** Keep it disabled if those users are not trusted. Loopback binding prevents direct remote connections, not access from other host accounts.

## Network and shared folders

**Shared NAT** connects the guest through the host; **Disconnected** disables its virtual network. While the VM is stopped, add TCP/UDP rules under **Loopback forwards** to expose a guest service on a host loopback port. Other local users can reach these forwarded services too, so retain authentication in the guest service. Optional vhost-net needs both host kernel support and device access.

Add a directory and tag under **Shared folders**, save settings and start the VM. In the guest, create a mount point and mount the tag; for a tag named `shared`:

```sh
sudo mkdir -p /mnt/shared
sudo mount -t 9p -o trans=virtio shared /mnt/shared
```

**Shares are writable.** The guest can modify or delete shared files within the host account's permissions; this backend has no read-only export. Use a dedicated exchange directory rather than your home directory. Access boundaries are described in the [security model](security.md).

## Shutdown, updates and data

```sh
gunyahvm stop ubuntu
gunyahvm restart ubuntu
```

`stop` requests a clean guest shutdown and waits for exit. **`stop --force` acts like cutting power and can lose data.** A failed guest shutdown is reported as a failure. Low battery, excessive temperature or host sleep also initiates shutdown; sleep may force termination after its deadline. Closing the manager only detaches it. Reopen the application to manage the running guest.

Stop all VMs before upgrading or removing the application. Release notifications open the download page; they do not install packages automatically. Disable checks in **Preferences** or launch with `GUNYAHVM_NO_UPDATE_CHECK=1`. Checks use a 24-hour cache and send no device information to GitHub.

VM data lives in `~/.local/share/gunyahvm/`, including disks under `vms/` and generated keys under `keys/`. Back up this directory with VMs stopped. Package removal preserves it. **`gunyahvm delete ubuntu` deletes the stopped VM and its disk**, so copy out needed data first.

With disk-managed boot files, the next start or guest-requested reset extracts kernel updates from the guest disk. With **Runtime guest kernel**, the kernel and modules instead come from the installed GunyahVM component package.
