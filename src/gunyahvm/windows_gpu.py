# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Windows with GPU acceleration: host requirements, driver payload and VMM profile.

The guest driver is anonymix007 / Droid-VM's WDDM display driver (virtio_wddm_rs)
with its D3D11 (DXVK) and D3D12 (vkd3d-proton) user-mode drivers and Turnip over
vdrm, packaged by Gl1ch5 (build-43) with Turnip rebuilt for the Adreno 830 chip ID
that drm/msm reports. It needs the Droid-VM VMM that the macOS profile ships: DRM
native context on the MSM render node, the guest's RAM shared with the host
(pseudo-unprotected; the restricted DMA pool stalls the GPU's transfers), the KMD's
boot command pool and a VRAM budget. Installation (WinPE and DISM) keeps the
protected basic-display profile; the installed system starts with the GPU.
"""

import base64
from copy import deepcopy
import subprocess

from . import components

# Boot command pool for the KMD, carved from the top of guest RAM (virtio-gpu
# feature DROIDVM_BOOT_POOL), and the VRAM budget it reports to Windows. Both are
# the values the community driver was brought up and measured with.
POOL_MIB = 256
VRAM_MIB = 2048
CERTIFICATE = "DroidVM-GPU-Test.cer"


def driver_component(runtime):
    return runtime.get("components", {}).get("windows_gpu")


def requirements(snapshot):
    """What this host lacks for the GPU profile; empty when it can run it.

    Before the VM drivers are loaded the kernel's memory sharing cannot be
    queried. Creation loads them and checks again, so an unknown answer is not
    reported as missing here.
    """
    reasons = []
    runtime = snapshot.runtime.get("components", {})
    if not driver_component(snapshot.runtime):
        reasons.append("The Windows GPU driver is not installed")
    vmm = set(runtime.get("macos", {}).get("features", []))
    if not {"vm-fd-share", "windows-wddm"} <= vmm:
        reasons.append("The GPU-capable VMM is not installed")
    if snapshot.protected_share is False or (
        snapshot.protected_share is None and not snapshot.drivers_loadable
    ):
        reasons.append("The host kernel cannot share guest memory with the VMM")
    if not any(n.get("driver") == "msm" and n.get("accessible") for n in snapshot.render_nodes):
        reasons.append("No accessible Adreno render node")
    if not snapshot.udmabuf:
        reasons.append("/dev/udmabuf is not accessible")
    return reasons


def boot_profile(cfg):
    """The profile this boot uses.

    While the installation media are attached, WinPE deployment keeps the
    protected basic-display profile: its storage and network drivers run
    through the restricted DMA pool, and no GPU driver runs before the
    installed system starts. Afterwards the configured profile applies.
    """
    if cfg["display"]["gpu"] == "native" and cfg["install_media"]:
        cfg = deepcopy(cfg)
        cfg["memory_mode"] = "protected"
        cfg["display"]["gpu"] = "simplefb"
    return cfg


def build(vm, cfg, rd, snapshot, wayland):
    """crosvm argv and environment for an installed Windows with the GPU."""
    reasons = requirements(snapshot)
    if snapshot.protected_share is not True:
        reasons.append("The host kernel cannot share guest memory with the VMM")
    if reasons:
        raise ValueError("GPU acceleration is unavailable: " + "; ".join(dict.fromkeys(reasons)))
    d = cfg["display"]
    if not wayland and not d["vnc"]:
        raise ValueError("A Wayland session or VNC display is required")
    files = snapshot.runtime["components"]["macos"]["files"]
    from .crosvmargs import display_refresh

    hz = display_refresh(d.get("output", ""))
    a = [
        files["binary"], "--no-syslog", "--log-level", "info", "--extended-status", "run",
        "--hypervisor", "gunyah", "--name", "gunyahvm-" + cfg["name"],
        "--cpus", str(cfg["cpus"]), "--mem", str(cfg["memory_mib"]),
        "--hugepages", "--no-pmu", "--no-smt", "--no-usb",
        "--protected-vm-pseudo-unprotected", "--prepare-lend-mthp-mode", "single",
        "--pre-alloc", f"drm-host-mb={POOL_MIB}", "--disable-sandbox",
        "--socket", str(rd / "control.sock"),
        "--block", str(vm / "disk.raw") + ",async-executor=uring,multiple-workers=true",
    ]
    if cfg["network_mode"] == "nat":
        a += ["--net", f"tap-name={cfg['tap']},mac={cfg['mac']}"
              + (",vhost-net" if cfg["vhost_net"] else "")]
    a += [
        "--serial",
        f"type=file,hardware=serial,num=1,path={rd}/serial.log,input={rd}/console.in,console,earlycon",
        # No firmware framebuffer: the firmware draws on virtio-gpu, the screen
        # the window shows. A separate boot framebuffer becomes a second
        # Windows display (the basic display driver), and with two adapters
        # the desktop never reaches the GPU's screen.
        "--gpu",
        "backend=virglrenderer,context-types=virgl:virgl2:drm,egl=true,glx=false,gles=true,"
        f"surfaceless=true,udmabuf=true,fixed-blob-mapping=false,vram-mb={VRAM_MIB},"
        f"displays=[[mode=windowed[{d['width']},{d['height']}],refresh-rate={hz}]]",
    ]
    if d["vnc"]:
        # The VNC binding brings its own tablet and keyboard for this screen.
        a += ["--vnc-server", f"host=127.0.0.1,port={d['vnc_port']},screen=gpu-0"]
    else:
        a += ["--wayland-sock", wayland]
        if d["keyboard"]:
            a.append("--display-window-keyboard")
        if d["mouse"]:
            a.append("--display-window-mouse")
    a.append(str(vm / "firmware.fd"))
    env = components.library_environment(snapshot.runtime, "macos")
    # Present by copying the guest's scanout into the window's buffers. The
    # dmabuf import route is not established through Casilda and Mutter yet.
    env["GPU_DISPLAY_COPY_MODE"] = "cpu"
    if d["mouse"] and pointer_input(snapshot.runtime):
        # Mouse and touchpad as an absolute pointer with every button and the
        # wheel; fingers as multi-touch. Otherwise the pointer is one touch.
        env["CROSVM_GVM_INPUT_MODE"] = "tablet"
    return a, env


def pointer_input(runtime):
    """Whether the GPU profile's VMM gives the window a full pointer and real touch."""
    return "window-pointer" in components.features(runtime, "macos")


INSTALL_SCRIPT = r"""$ErrorActionPreference = 'Stop'
$dir = 'C:\gunyahvm\wingpu'
foreach ($store in @('Root', 'TrustedPublisher')) {
  certutil.exe -addstore -f $store (Join-Path $dir 'DroidVM-GPU-Test.cer') | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "GPU driver certificate import into $store failed" }
}
$boot = (bcdedit.exe /enum '{current}') -join "`n"
if ($boot -notmatch 'testsigning\s+Yes') {
  bcdedit.exe /set '{current}' testsigning on | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'Could not enable test signing for the GPU driver' }
}
pnputil.exe /add-driver (Join-Path $dir 'driver\virtio_wddm_rs.inf') /install | Out-Null
# 259: staged, no matching device yet; the GPU appears from the next start.
if ($LASTEXITCODE -notin 0, 259, 3010) { throw "pnputil failed with $LASTEXITCODE" }
'GUNYAHVM_GPU_DRIVER_STAGED'
"""


def install_driver(vm, cfg, ip, runtime):
    """Stage the GPU driver in a running Windows over the existing control channel.

    For a VM created before its installation media carried the driver. The
    driver binds when the VM next starts with GPU acceleration.
    """
    from . import windows_agent

    component = driver_component(runtime)
    if not component:
        raise ValueError("The Windows GPU driver is not installed")
    source = components.payload(runtime, "windows_gpu", "inventory").parent
    ssh = windows_agent.win_ssh_argv(
        [vm / "agent-key"], cfg["windows"]["ssh_user"], ip, vm / "known_hosts", "")[:-1]
    prepare = subprocess.run(
        ssh + ["powershell -NoProfile -Command \"New-Item -ItemType Directory -Force "
               "C:\\gunyahvm\\wingpu | Out-Null\""],
        capture_output=True, text=True, timeout=30)
    if prepare.returncode:
        raise ValueError("Windows refused the GPU driver copy: " + prepare.stderr.strip())
    # scp takes the same options as ssh except the destination.
    scp = ["scp", "-q", "-r"] + ssh[1:-1] + [
        str(source / "driver"), str(source / CERTIFICATE),
        f"{ssh[-1]}:C:/gunyahvm/wingpu/"]
    copied = subprocess.run(scp, capture_output=True, text=True, timeout=300)
    if copied.returncode:
        raise ValueError("Copying the GPU driver failed: " + copied.stderr.strip())
    script = base64.b64encode(INSTALL_SCRIPT.encode("utf-16le")).decode("ascii")
    installed = subprocess.run(
        ssh + ["powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand " + script],
        capture_output=True, text=True, timeout=180)
    if installed.returncode or "GUNYAHVM_GPU_DRIVER_STAGED" not in installed.stdout:
        raise ValueError("Windows could not install the GPU driver: "
                         + (installed.stderr or installed.stdout).strip()[-800:])
    return component["version"]
