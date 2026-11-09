<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Security model

This guide explains who can start VMs, what privileges their processes retain, and what network and folder sharing expose to guests and other users.

## Device access and authorization

udev assigns `/dev/gunyah` and `/dev/vhost-net` to `root:gunyah` with mode `0660` and logind's `uaccess` tag. Active local desktop users receive an ACL. For a remote account, an administrator can run `sudo usermod -aG gunyah USERNAME`; the account must log out and back in. Group membership grants VM operations without an active local desktop session, so add only trusted users.

ACLs are per UID: an SSH process using the authorized desktop account shares its device access. Switching seats revokes future opens, not file descriptors that are already open. Neither device is made world-writable.

The polkit helper runs Python in isolated mode and exposes only `load`, `net-up`, `net-down`, `run` and read-only `firmware-info`. Driver loading, network creation and launch require an active local seat or `gunyah` group membership. Network cleanup remains available after the seat locks, but can remove only the caller's UUID-derived profile. The helper checks mount and network namespaces and never reads user VM configuration or disks while root.

## Runtime privileges and sandboxing

The launcher sets a finite locked-memory limit derived from total RAM, initializes groups and drops all real, effective and saved GIDs and UIDs. It then enters a fixed installed supervisor. crosvm, its renderer and the supervisor run as the ordinary account. No setuid application, file capabilities, `CAP_IPC_LOCK` or host `CAP_NET_ADMIN` is installed. The per-process memory ceiling and per-user VM lock do not form a global multi-user memory quota.

Software display and plain virgl retain crosvm's minijail device sandbox. If a runtime exposes GuestAccept or shared-memory modes, selecting those modes disables the device sandbox because that interface requires a single process. Execution still uses the ordinary account. A sandbox failure never silently selects a less restricted mode.

AppArmor grants the installed crosvm executable the user-namespace operation needed by minijail. Its unconfined profile is not a separate filesystem jail. The account's own permissions still matter when deciding which guests and shared directories to trust.

## Network and display exposure

TAP names, MAC addresses and NetworkManager UUIDs derive from both the VM UUID and caller UID. The helper accepts canonical UUIDs, RFC1918 /24 networks and a fixed network-acceleration choice. It refuses overlapping routes and mismatched existing interfaces. Commands use argument arrays and a fixed environment.

Port forwards bind to loopback only. Other local accounts can connect to those ports; guest services must enforce their own authentication.

**VNC also binds to `127.0.0.1`, with no authentication.** Any local account able to connect can view the display and send input to the guest. Keep VNC disabled on hosts with untrusted local users. Binding to loopback is not a boundary between accounts.

## Shared files and guest control

Linux 9p shares are writable under the host account's permissions. The guest can change or delete shared files. This backend does not offer read-only exports; use a dedicated exchange folder and keep private keys, credentials and unrelated documents outside it.

Guest control messages have a size limit, and reported IP addresses must belong to the VM subnet. SSH pins host keys in a separate `known_hosts` file for each VM and refuses changed keys. A changed key therefore requires checking the guest's identity before replacing the stored key.

## Host integration and teardown

The module loader requests only installed, missing VM-management/function modules and optional vhost-net. `modprobe` resolves dependencies. It does not install kernel support or guess platform/trusted-VM hooks. tmpfiles enables shmem THP `advise`.

Host sleep uses a logind delay inhibitor for guest shutdown and network cleanup. If the deadline expires, forced termination is recorded as a failure; guest data can be lost. Shut down guests before sleep when they contain unsaved work.

Package changes refuse a running installed VMM or supervisor, preserve user data and do not unload host modules. Installation inside a chroot skips live udev, sysfs and AppArmor changes. Report vulnerabilities through [Security reporting](../SECURITY.md).

Thermal protection reads every zone with a passive trip from the running kernel's sysfs. Startup is refused when any monitored zone reaches its passive temperature, with a throttling message. During operation, reaching that zone's hot temperature requests a clean shutdown; when hot is absent, the threshold is critical minus 5°C. Forced termination occurs only after the existing shutdown deadline. Messages and state records identify the zone, reading and trip; battery protection is unchanged.
