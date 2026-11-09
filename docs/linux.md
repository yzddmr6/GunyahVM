<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Linux guests

## Create and connect

Open GunyahVM to choose the Ubuntu catalogue or a local ARM64 raw/qcow2 disk. The CLI uses the same core:

```sh
gunyahvm load
gunyahvm create ubuntu --mirror official --start
gunyahvm list
gunyahvm console ubuntu
gunyahvm ssh ubuntu
```

`--mirror tuna` and `--mirror ustc` select Chinese download sources; checksums still come from Ubuntu's official immutable build directory. `--release YYYYMMDD` selects a build. A local disk uses `--image PATH`; a custom matching kernel/initrd directory uses `--boot-dir PATH`. The default Ubuntu route extracts the kernel/initrd from the guest disk. No existing VM configuration format is imported.

A private SSH key is generated if `--ssh-key` is omitted. Cloud-init provisions the guest control channel and password-locked SSH account. Wait for the VM's `guest_ready` and `ipv4` fields before connecting. Ctrl-C detaches log viewing; Ctrl-] detaches `console --interactive`. Reconnection replays at most 256 KiB.

## Graphics and sharing

Settings offers only graphics modes permitted by the host and installed runtime. 3D/2D virtio-GPU and shared memory require the matching runtime guest kernel. For example, when available:

```sh
gunyahvm create desktop --guest-payload --gpu virgl --start
```

App-created Ubuntu cloud desktops install a minimal desktop and enable autologin for the provisioned guest user. Imported disk images must supply their own desktop/cloud-init integration.

Add writable host folders and loopback port forwards in Settings while the VM is stopped. A Linux guest can mount a share with `sudo mount -t 9p -o trans=virtio TAG DIRECTORY`; permissions remain bounded by the host account. vhost-net is optional and requires both kernel and device access. VNC can replace the local Wayland window; it listens only on localhost.

## Stop and maintain

```sh
gunyahvm stop ubuntu
gunyahvm restart ubuntu
gunyahvm delete ubuntu
```

`stop` waits for clean shutdown and reports failure when the guest agent cannot confirm it. `stop --force` is an explicit crash stop. Low charge, overheating or host sleep initiates shutdown; sleep has a bounded fallback to forced termination. Close the management window to detach without stopping. Stop VMs before updating or removing the application.

With the disk-managed kernel, a stopped VM's next start extracts guest kernel updates; guest-requested resets also refresh the disk-managed boot files. With a runtime kernel, its version and modules come from the component package. User data remains after package removal.
