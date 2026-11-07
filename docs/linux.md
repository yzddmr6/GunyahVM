<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# Linux user guide

## Create

Install `gunyahvm`, check the host, run `gunyahvm load`, then use an existing SSH public key:

```sh
gunyahvm create ubuntu --ssh-key ~/.ssh/id_ed25519.pub --disk-gib 16 --start
gunyahvm list
gunyahvm console ubuntu
```

Creation first checks read/write access to `/dev/gunyah` and polkit authorization for VM launch and networking. Denied or unavailable authorization stops creation before any image download or VM directory creation. Launch checks authorization again. Creation downloads the selected Ubuntu ARM64 cloud image with its upstream checksum; the default immutable image release is recorded in `gunyahvm create --help`. The guest uses public-key login and a password-locked account. The private key is never copied into the guest. Override resources with `--cpus`, `--memory-mib`, `--disk-gib`, and `--username`. RAM defaults follow host memory and online CPUs; available RAM is checked at launch. An explicit `--image` can use `--boot-dir` containing a matching `Image`, `initrd.img` and optional `cmdline`.

## Operate

```sh
gunyahvm ssh ubuntu
gunyahvm ssh ubuntu -- 'sudo apt-get update'
gunyahvm stop ubuntu
gunyahvm start ubuntu
gunyahvm plan ubuntu
```

Wait for `list` to report guest readiness and its IP before SSH. Ctrl-C detaches the console viewer. `console --interactive` passes terminal input; it does not unlock the guest's password account. `plan` prints launch arguments without starting the VM. Guest kernel updates are extracted from the stopped disk on the next start; an in-guest reboot does not refresh those host boot files. The previous boot files are retained.

`stop` requests guest shutdown, waits for exit and cleans up the owned NAT connection. Run it after an in-guest shutdown too. `stop --force` may lose unwritten data. An interrupted supervisor is reported as interrupted; its surviving crosvm must exit before a new start. One VM per user is enforced by a runtime lock. NetworkManager creates transient caller-owned TAP/shared connections; it refuses route overlap.

## Data and removal

Data lives in `~/.local/share/gunyahvm/vms/NAME/`; `--store` must stay inside your home. Configuration, disk, boot files, state and run logs belong to the VM owner. Back up stopped VM directories; sparse disks grow with writes. Inspect run logs before sharing them because guest output may contain private information.

```sh
gunyahvm stop ubuntu
gunyahvm delete ubuntu
sudo apt purge gunyahvm
```

`delete` permanently removes the named guest. Package removal preserves home-directory data and refuses while crosvm is running. The group is retained for administrator management. Reboot clears loaded modules, the AppArmor grant and the package's runtime hugepage setting; do not unload active Gunyah drivers manually.

## Troubleshooting

Run `gunyahvm check`; every failed item has a repair hint. `load` only enables the fixed module list. An SSH user must be in `gunyah`; an active local-seat user needs no persistent group change. A low shell memlock limit is repaired for a new VM by the authorized launcher. Sandboxing failures never fall back to disabling the sandbox. Keep the host awake and stop guests before shutdown.
