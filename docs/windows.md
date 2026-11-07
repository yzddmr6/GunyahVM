<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Windows guests

## Requirements

Supply an official Windows 11 ARM64 ISO and your own licence. Windows assets are installed by default. Use at least 4608 MiB guest memory, a sparse 64 GiB disk and enough space for installation media plus an 8 GiB host reserve. The default display is software simplefb over loopback VNC; Windows 3D, vTPM and Secure Boot are not provided. Drivers use DroidVM's public test certificate and Windows Test Mode.

## Create and install

Use the GTK creation wizard, or:

```sh
gunyahvm create windows --os windows11 --iso ~/Downloads/Windows11_ARM64.iso --edition Professional --start
gunyahvm view windows
```

Choose an edition actually present in the ISO. WinPE partitions only the new VM disk, applies the selected WIM with DISM, installs the bundled drivers and copies the deployment payload. The supervisor requires a matching completion token before removing installation media and booting the installed disk into OOBE. A failed deployment is not silently retried.

Normal OOBE creates your interactive Windows account. For an unattended installation, `--local-user vmuser --password-fd 3` can read a password from an already-open descriptor; omitting `--password-fd` uses hidden terminal input. The password is not passed on the command line. Windows answer-file encoding is not encryption: protect the VM directory. Answer files are removed at first login. No automatic Windows login is enabled.

## Guest control and display

The app provisions a dedicated SSH control account with a per-VM key. With an explicit local account this runs during setup; otherwise it runs after the owner's first login, so the control account cannot become the only usable Windows account. Guest setup installs OpenSSH Server from Windows Update when it is missing, disables password SSH and restricts SSH/RDP firewall access to the host gateway. Internet access inside the guest is needed for a missing OpenSSH capability.

The overview reports agent readiness and the guest address. `gunyahvm stop windows` requests shutdown through authenticated SSH and waits for VMM exit. A refused key, absent agent or timeout is a failure, not a clean stop. `gunyahvm rdp windows` opens the RDP client; use your interactive Windows credentials and a Windows edition that supports RDP hosting. `view` reconnects to VNC for setup or recovery. Other local host users can reach loopback VNC.

## Lifecycle

Windows-requested resets retain the same VM lock/network. `restart` first waits for clean shutdown. `stop --force` performs an explicit crash stop and can lose guest data. Low power, excessive temperature and host sleep initiate shutdown; the bounded sleep fallback records forced termination if needed. Host package removal preserves the VM's data. Linux 9p folder exports are not offered to Windows because the bundled Windows drivers contain no 9p client.
