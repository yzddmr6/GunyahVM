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

## GPU acceleration

New Windows VMs use GPU acceleration whenever the device supports it. Windows draws its desktop, and Direct3D 11 programs render, with the tablet's Adreno GPU. The guest driver is the WDDM display driver from anonymix007 and the Droid-VM team, packaged by Gl1ch5 (build-43), with Turnip rebuilt to recognize the Adreno 830. Device Manager lists it as "Qualcomm Adreno 829 (DroidVM, unofficial build)".

Installation media stage the driver. Windows setup (installation, the first start and the first sign-in) runs on the basic display, because setup's pages do not draw with this driver. After the first sign-in the VM page says that GPU acceleration starts the next time the VM starts, and from then on it does.

Choose **Basic Display** under **Settings → Display → Graphics** to draw with the processor instead, for example when a Windows update shows setup pages again, and **GPU Acceleration** to return. After a VM has once started with GPU acceleration, its basic display also shares the VM's memory with the host. A Windows VM created with an earlier GunyahVM has no GPU driver: start it, wait until it is ready, choose **Install GPU Driver** in its menu (or run `gunyahvm install-gpu-driver NAME`), then restart it. The driver is copied and installed through the VM's control account. When the GPU driver is installed into an older VM, a one-time signing certificate is created in the VM and its private key is deleted right away.

GPU acceleration needs a system kernel that can share virtual machine memory, access to the Adreno render node and `/dev/udmabuf`. Without them, new VMs use the basic display and **Settings** says what is missing.

## Control and shut down

GunyahVM creates a separate control account with a per-VM SSH key and allows control-port access only from the host gateway. The account is hidden from Windows sign-in; hiding its tile does not disable SSH or network logon. Your interactive local account remains visible.

The application shows the running state and guest IP. Clean shutdown uses SSH and waits for Windows to exit before removing the network. Click **Shut Down** in the application, or run:

```sh
gunyahvm stop windows
```

Keep the guest virtual NIC and control service available. Force stop acts like cutting power and may damage guest data.

## Network and display limits

- Installation can remain fully offline. The default virtual network still uses host shared NAT, which is not Internet isolation. To keep the guest offline, restrict external forwarding for that virtual network on the host while retaining host-to-guest SSH control. Disabling the entire virtual network also removes the SSH path needed for clean shutdown.
- The basic display is a software framebuffer. Windows guests have no shared folders.
- With GPU acceleration the VM's memory is shared with the host instead of protected from it, as the GPU's command and data transfers require. The mouse and touchpad move a pointer with every button and scrolling, and fingers touch the screen; the basic display's touch input modes do not apply. After the application itself restarts, the display of a running VM cannot be reattached; use Remote Desktop or restart the VM.
- With GPU acceleration, Direct3D 11 programs run on the GPU. Direct3D 12 works up to feature level 11_1; programs that require 12_0 do not start. Vulkan programs inside Windows do not run, because the driver's Vulkan loader cannot create an instance. A few small interface elements, such as some labels and tray icons, sometimes show as solid blocks. The screen keeps the resolution chosen in **Settings**; maximizing the window or full screen scales it.
- Guest Secure Boot and vTPM are not provided. Guest drivers, including the GPU driver, are test-signed, so Windows runs in test mode and shows a "Test Mode" mark on the desktop.
- Updating host packages does not modify deployed Windows disks or old installation media. Recreate the media and VM to use updated first-configuration logic.

## Investigate deployment errors

Preserve the error state and read `C:\gunyahvm\first-boot.log` and `openssh-install*.log` in the guest. The host VM directory keeps DISM and offline driver logs in `deployment-logs`. Resolve the earliest reported error before deploying again; do not use force stop to hide installation failure.

Battery level and passive throttling allow normal VM operation. When a monitored zone reaches its kernel hot trip (critical if hot is absent), the application declines startup or requests a clean guest shutdown.
