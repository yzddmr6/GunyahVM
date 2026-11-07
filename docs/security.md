<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# Security model

## Authorization

The `gunyah` system group is administrator-managed. udev creates `/dev/gunyah` as `root:gunyah 0660`, tags it with `uaccess` before systemd's seat rule, and logind grants an ACL to the active local-seat user. No world-writable device or persistent automatic group membership is used. Polkit grants only the three exact GunyahVM actions to an active local subject with a seat or to a `gunyah` group member. Other callers are denied without an administrator-authentication fallback. SSH users need the group.

ACLs and resource limits belong to UIDs/processes, not terminal connections: an SSH process using the same UID as an already-authorized desktop user shares that user's discretionary access. Linux cannot make those processes separate security principals using uaccess. Use a separate Unix account for an untrusted remote login. Seat switching removes the ACL for future opens; existing device FDs and running VMs are not revoked. Group members are continuously trusted.

## Finite memlock without a privileged VMM

A normal desktop user need not log out or modify PAM settings. On start, polkit runs the fixed `gunyahvm-run` helper. It reads only kernel state and root-owned device limits, computes at most 6 GiB and no more than MemTotal minus a 25%/1 GiB reserve, sets both memlock limits, then initializes groups and drops all saved, effective and real UID/GID values. Only then does it exec the fixed installed Python CLI. No user-supplied executable, Python import directory or VM file is used while root. The supervisor and crosvm inherit the finite limit. They receive no file capabilities, setuid bit, CAP_IPC_LOCK or CAP_NET_ADMIN. Group PAM limits provide a finite session default, but the launcher also works with existing user managers.

This is a per-process resource bound, not a global multi-user memory quota. Trusted users can start processes independently or consume memory outside GunyahVM; use host-wide administrative resource policy when multiple mutually untrusted users are present.

## Privileged helpers

The module loader accepts no arguments, verifies live Gunyah firmware and the host mount namespace, rejects initrd and unsupported platform plugins, loads only fixed modules with a clean environment and checks device ownership. It does not trust a board-specific mount marker or unload drivers.

The network helper accepts only up/down, a UUID, an RFC1918 IPv4 /24 and an optional fixed vhost-net flag. The caller comes from polkit, not a supplied username. It uses the host network namespace, a fixed command search path, caller-owned NetworkManager profiles, and refuses overlapping host routes or mismatched existing interfaces. It does not execute a shell or load arbitrary modules. Clean up a VM's network before losing the active seat, or return to that seat; group membership remains the remote administration route.

Helpers use isolated Python mode. The VMM uses minijail and an explicit `/usr/share/gunyahvm/crosvm/empty` pivot root. Its AppArmor profile grants the user namespace operation required by minijail; the unconfined AppArmor flag is not an independent filesystem sandbox. No failure enables `--disable-sandbox` automatically.

## Integration and guests

Dracut excludes VM management modules from initrd; loading is explicit. tmpfiles sets shmem THP to `advise`, affecting only mappings that request hugepages. Package installation skips live udev/sysfs/AppArmor actions in a chroot. Package removal never kills a VM or unloads its modules. It preserves user data and the group.

Guests, ISOs, disks and guest control output are untrusted. Review logs before sharing. Windows runs test-signed drivers with no vTPM or guest Secure Boot, and its loopback VNC endpoint permits other local users to connect. Do not forward it to untrusted networks. These limitations are independent of Gunyah's protected-memory mode. Report vulnerabilities through [SECURITY](../SECURITY.md).

## References

[Polkit authorization model](https://www.freedesktop.org/software/polkit/docs/latest/polkit.8.html), [Linux resource limits](https://man7.org/linux/man-pages/man2/getrlimit.2.html), and the installed systemd `73-seat-late.rules` define the integration boundaries.
