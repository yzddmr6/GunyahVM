<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Windows guests

Create a VM from your own Windows 11 ARM64 ISO, install offline with a local account, then use the embedded display for the sign-in screen and key-authenticated SSH for guest control.

Windows guests run on Xiaomi Pad 8 Pro with system release 1.1.1 or later.

## Prepare the image and resources

Supply a valid Windows 11 ARM64 ISO and an applicable licence. GunyahVM supplies neither Windows images nor activation services. Select an edition present in your ISO; Pro is the default, and you can enter an edition name or image index under **Advanced** when creating the VM.

Windows needs at least 4608 MiB of guest RAM and a 64 GiB virtual disk, with 4 virtual CPUs by default. Leave enough host memory for the system and display. The virtual disk grows on demand, so deployment also needs sufficient free physical storage.

## Install through the application

1. Click **+**, select **Windows 11** and choose the ISO file.
2. Enter an offline local username and password, and confirm the password. This is the Windows sign-in account, separate from the VM control account.
3. Confirm memory, CPU and disk settings, then click **Create**. Keep the application open while it prepares installation media.
4. Select the VM and click **Start**. Deployment applies the image, installs drivers, removes the installation media automatically and boots Windows.
5. Click **Open Display** and complete region, keyboard, licence and privacy choices in the GunyahVM window to reach local-account sign-in.

First configuration installs OpenSSH from the pinned offline package on the installation media. It does not download features through Windows Update. The local-account path needs neither a Microsoft account nor an Internet connection.

## Install through the command line

```sh
gunyahvm create windows --os windows11 --iso /path/to/windows-arm64.iso --local-user vmuser
gunyahvm start windows
gunyahvm view windows
```

The create command prompts for the local-account password without echoing it. Automation can use `--password-fd`; do not put passwords in command-line arguments. Use `--edition` for an edition name or index. The application and CLI share the VM configuration.

## Control and shut down

GunyahVM creates a separate control account with a per-VM SSH key and allows control-port access only from the host gateway. The account is hidden from Windows sign-in; hiding its tile does not disable SSH or network logon. Your interactive local account remains visible.

The application shows the running state and guest IP. Clean shutdown uses SSH and waits for Windows to exit before removing the network. Click **Shut Down** in the application, or run:

```sh
gunyahvm stop windows
```

Keep the guest virtual NIC and control service available. Force stop acts like cutting power and may damage guest data.

## Network and display limits

- Installation can remain fully offline. The default virtual network still uses host shared NAT, which is not Internet isolation. To keep the guest offline, restrict external forwarding for that virtual network on the host while retaining host-to-guest SSH control. Disabling the entire virtual network also removes the SSH path needed for clean shutdown.
- Windows uses a software framebuffer in the application window. It does not provide virgl, other guest 3D acceleration or shared folders.
- Guest Secure Boot and vTPM are not provided. Guest drivers use the supplied test-signing configuration.
- Updating host packages does not modify deployed Windows disks or old installation media. Recreate the media and VM to use updated first-configuration logic.

## Investigate deployment errors

Preserve the error state and read `C:\gunyahvm\first-boot.log` and `openssh-install*.log` in the guest. The host VM directory keeps DISM and offline driver logs in `deployment-logs`. Resolve the earliest reported error before deploying again; do not use force stop to hide installation failure.

Thermal protection reads every zone with a passive trip from the running kernel's sysfs. Startup is refused when any monitored zone reaches its passive temperature, with a throttling message. During operation, reaching that zone's hot temperature requests a clean shutdown; when hot is absent, the threshold is critical minus 5°C. Forced termination occurs only after the existing shutdown deadline. Messages and state records identify the zone, reading and trip; battery protection is unchanged.
