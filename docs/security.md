<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Security model

## Authorization

udev assigns `/dev/gunyah` and `/dev/vhost-net` to `root:gunyah 0660` and tags them for logind uaccess. Active local-seat users receive an ACL; administrators can explicitly grant remote accounts group membership. No device is made world-writable. ACLs are per UID, so an SSH process sharing an authorized desktop UID shares its discretionary access. Seat changes revoke future opens, not existing file descriptors.

One isolated-Python polkit helper exposes `load`, `net-up`, `net-down` and `run`. Driver load, network creation and launch require an active local seat or the `gunyah` group. Network cleanup remains available to an identified ordinary caller after the seat locks, but can remove only that caller's UUID-derived profile. The helper verifies both mount and network namespaces and never reads user VM configuration or disks while root.

## Ordinary-user runtime

The launcher sets a finite memlock limit derived from total RAM, initializes groups, drops all real/effective/saved GIDs and UIDs, and enters a fixed installed supervisor. The VMM, renderer and supervisor use the same ordinary account. No setuid application, file capabilities, CAP_IPC_LOCK or host CAP_NET_ADMIN is installed. The per-process ceiling and per-user VM lock are not a global multi-user memory quota.

Normal modes retain crosvm's minijail device sandbox. The current GuestAccept/shared-memory interface requires a single process and explicitly disables that device sandbox for those selected modes; it still runs as the ordinary user. A sandbox error never silently switches modes. AppArmor grants the installed crosvm executable the user-namespace operation needed by minijail; its unconfined profile is not an independent filesystem jail.

## Networks, sharing and guests

TAP names, MACs and NetworkManager UUIDs derive from both the VM UUID and caller UID. The helper accepts only canonical UUIDs, RFC1918 /24 networks and a fixed acceleration choice. It refuses overlapping routes and mismatched existing interfaces. Commands use argument arrays and a fixed environment. User port forwards bind only to loopback.

Linux 9p shares grant the guest access under the host user's permissions. The bundled backend exports writable folders; it does not pretend to implement a read-only option that crosvm lacks. Select folders accordingly. Guest control messages are bounded, and IP addresses must belong to the VM subnet. SSH pins host keys in each VM's own known_hosts file and refuses changed keys.

Windows uses test-signed drivers and Test Mode, without a vTPM or guest Secure Boot. Its dedicated control account accepts the app's key, with password SSH disabled and firewall access limited to the host gateway. RDP uses Windows credentials. Loopback VNC is accessible to other local accounts; do not forward it to an untrusted network. Guest images, ISO files and logs remain untrusted input.

## Integration and teardown

The module loader requests only installed, currently missing VM-management/function modules and optional vhost-net. Kernel dependencies are resolved by modprobe; optional platform/trusted-VM hooks are not guessed. tmpfiles enables shmem THP `advise`. Sleep uses a logind delay inhibitor with enough time for bounded guest shutdown and cleanup; forced termination is recorded as a failure. Package installation in a chroot skips live udev/sysfs/AppArmor changes. Package changes refuse an active installed VMM/supervisor, preserve user data and do not unload host modules.

Report security problems through [SECURITY](../SECURITY.md).
