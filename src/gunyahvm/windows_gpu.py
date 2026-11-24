# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Windows with GPU acceleration: host requirements, driver payload and VMM profile.

The guest driver is anonymix007 / Droid-VM's WDDM display driver (virtio_wddm_rs)
with its D3D11 (DXVK) and D3D12 (vkd3d-proton) user-mode drivers and Turnip over
vdrm, packaged by Gl1ch5 (build-43) with Turnip rebuilt for the Adreno 830 chip ID
that drm/msm reports and the D3D11 driver rebuilt so that DXVK does not emit 16-bit
shader inputs and outputs, which Turnip lacks. It needs the Droid-VM VMM that the
macOS profile ships: DRM native context on the MSM render node, the guest's RAM
shared with the host (pseudo-unprotected; the restricted DMA pool stalls the GPU's
transfers), the KMD's boot command pool and a VRAM budget.

Windows setup runs on the protected basic display: WinPE deployment, the first
start and the first sign-in. The staged driver stays dormant there (there is no
virtio-gpu device), and the setup pages do not draw with it. When the first
sign-in has finished, the VM uses the GPU from its next start. Once a VM has
started with shared memory, its basic display keeps shared memory as well: the
same VMM without virtio-gpu, so Windows falls back to its basic display adapter.

Every profile puts the system disk, the network card, the GPU and WinPE's media
in the same PCI slots (windows_vm.PCI), so Windows keeps its disk and network
card, and their settings, whichever profile starts.
"""

import base64
import calendar
from copy import deepcopy
import re
import subprocess
import time

from . import components

# Boot command pool for the KMD, carved from the top of guest RAM (virtio-gpu
# feature DROIDVM_BOOT_POOL), and the VRAM budget it reports to Windows. Both are
# the values the community driver was brought up and measured with.
POOL_MIB = 256
VRAM_MIB = 2048
CERTIFICATE = "DroidVM-GPU-Test.cer"
# Written by the first sign-in's script when Windows setup has finished; the
# installer of an existing VM writes it once someone has signed in.
SETUP_MARKER = r"C:\gunyahvm\setup-complete"


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


def gpu_boot(cfg):
    """Whether an installed Windows starts with the GPU this time."""
    w = cfg["windows"]
    return (cfg["display"]["gpu"] == "native" and not cfg["install_media"]
            and bool(w.get("gpu_driver")) and bool(w.get("setup_done")))


def boot_profile(cfg):
    """The profile this boot uses.

    Until Windows setup has finished (installation media attached, or no first
    sign-in yet), a VM set to GPU acceleration starts on the basic display. A
    VM that has never started with shared memory keeps the protected profile
    for it; one that has keeps shared memory (see shared_basic).
    """
    if cfg["display"]["gpu"] == "native" and not gpu_boot(cfg):
        cfg = deepcopy(cfg)
        cfg["display"]["gpu"] = "simplefb"
        if not cfg["windows"].get("shared_boot"):
            cfg["memory_mode"] = "protected"
    return cfg


def shared_basic(cfg):
    """Whether this boot is the basic display with shared memory.

    After a start with shared memory, a protected start of the same Windows
    fails: the host's block device faults on guest memory at the boot manager
    and Windows falls into recovery. The way back to the basic display
    therefore keeps shared memory and only leaves out virtio-gpu.
    """
    return cfg["display"]["gpu"] == "simplefb" and cfg["memory_mode"] == "shared"


def memory_mode(cfg, gpu):
    """The memory mode for a Windows VM set to this graphics mode.

    GPU acceleration shares the guest's RAM. The basic display stays protected
    until Windows has once started with shared memory; after that only a shared
    start of it works.
    """
    return "shared" if gpu == "native" or cfg["windows"].get("shared_boot") else "protected"


# Windows recovers a display driver that stops answering (TDR) by resetting its
# device, and the VMM logs every reset of virtio-gpu ("gpu reset: scanout ...").
# When the host GPU hangs on what the guest submits, Windows resets it every few
# seconds until more than TdrLimitCount (5) TDRs fall within TdrLimitTime (60 s),
# stops with bug check 0x116 (VIDEO_TDR_FAILURE) and restarts; the same desktop
# hangs the GPU again after the next sign-in (GitHub issue #1). The driver's own
# start resets the device once, so one reset is part of every GPU boot.
TDR_RESETS = 3
TDR_WINDOW = 60
TDR_BEFORE_EXIT = 120
TDR_TAIL = 1 << 20
_LOG_TIME = re.compile(rb"\[(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)")


def tdr_loop(log):
    """Whether a run's VMM log ends in Windows' TDR loop.

    TDR_RESETS resets of virtio-gpu within TDR_WINDOW seconds, the last of them
    at most TDR_BEFORE_EXIT seconds before the VMM's last line. Only the log's
    last TDR_TAIL bytes are read.
    """
    try:
        with open(log, "rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - TDR_TAIL))
            lines = stream.read().splitlines()
    except OSError:
        return False
    resets, last = [], None
    for line in lines:
        stamp = _LOG_TIME.match(line)
        if not stamp:
            continue
        last = calendar.timegm(time.strptime(stamp[1].decode(), "%Y-%m-%dT%H:%M:%S"))
        if b"gpu reset: scanout" in line:
            resets.append(last)
    return (
        len(resets) >= TDR_RESETS
        and resets[-1] - resets[-TDR_RESETS] <= TDR_WINDOW
        and last - resets[-1] <= TDR_BEFORE_EXIT
    )


def basic_display(cfg):
    """CFG set to the basic display, as Settings would set it (see shared_basic)."""
    cfg = deepcopy(cfg)
    cfg["display"]["gpu"] = "simplefb"
    cfg["memory_mode"] = memory_mode(cfg, "simplefb")
    return cfg


def shared_requirements(snapshot):
    """What this host lacks for Windows with shared memory and the basic display."""
    reasons = []
    vmm = set(snapshot.runtime.get("components", {}).get("macos", {}).get("features", []))
    if "vm-fd-share" not in vmm:
        reasons.append("The VMM for shared memory is not installed")
    if snapshot.protected_share is False or (
        snapshot.protected_share is None and not snapshot.drivers_loadable
    ):
        reasons.append("The host kernel cannot share guest memory with the VMM")
    return reasons


def build(vm, cfg, rd, snapshot, wayland):
    """crosvm argv and environment for an installed Windows with shared memory.

    With GPU acceleration the screen is virtio-gpu; on the basic display it is
    the firmware framebuffer and there is no virtio-gpu device.
    """
    gpu = cfg["display"]["gpu"] == "native"
    reasons = requirements(snapshot) if gpu else shared_requirements(snapshot)
    if snapshot.protected_share is not True:
        reasons.append("The host kernel cannot share guest memory with the VMM")
    if reasons:
        what = "GPU acceleration" if gpu else "The basic display with shared memory"
        raise ValueError(what + " is unavailable: " + "; ".join(dict.fromkeys(reasons)))
    d = cfg["display"]
    if not wayland and not d["vnc"]:
        raise ValueError("A Wayland session or VNC display is required")
    files = snapshot.runtime["components"]["macos"]["files"]
    from .crosvmargs import display_refresh
    from .windows_vm import pci

    hz = display_refresh(d.get("output", ""))
    a = [
        files["binary"], "--no-syslog", "--log-level", "info", "--extended-status", "run",
        "--hypervisor", "gunyah", "--name", "gunyahvm-" + cfg["name"],
        "--cpus", str(cfg["cpus"]), "--mem", str(cfg["memory_mib"]),
        "--hugepages", "--no-pmu", "--no-smt", "--no-usb", "--no-rng",
        "--protected-vm-pseudo-unprotected", "--prepare-lend-mthp-mode", "single",
    ] + (["--pre-alloc", f"drm-host-mb={POOL_MIB}"] if gpu else []) + [
        "--disable-sandbox",
        "--socket", str(rd / "control.sock"),
        "--block", str(vm / "disk.raw") + ",async-executor=uring,multiple-workers=true" + pci("disk"),
    ]
    if cfg["network_mode"] == "nat":
        a += ["--net", f"tap-name={cfg['tap']},mac={cfg['mac']}"
              + (",vhost-net" if cfg["vhost_net"] else "") + pci("net")]
    a += [
        "--serial",
        f"type=file,hardware=serial,num=1,path={rd}/serial.log,input={rd}/console.in,console,earlycon",
    ]
    if not gpu:
        if d["vnc"] or not wayland:
            raise ValueError("The basic display with shared memory needs the application's window")
        # Windows' basic display adapter draws on the firmware framebuffer,
        # which this VMM shows in the window itself.
        a += ["--simplefb", f"width={d['width']},height={d['height']},poll-hz={hz},wayland-socket={wayland}"]
    else:
        # No firmware framebuffer: the firmware draws on virtio-gpu, the screen
        # the window shows. A separate boot framebuffer becomes a second
        # Windows display (the basic display driver), and with two adapters
        # the desktop never reaches the GPU's screen.
        a += [
            "--gpu",
            "backend=virglrenderer,context-types=virgl:virgl2:drm,egl=true,glx=false,gles=true,"
            f"surfaceless=true,udmabuf=true,fixed-blob-mapping=false,vram-mb={VRAM_MIB}"
            + pci("gpu") + ","
            f"displays=[[mode=windowed[{d['width']},{d['height']}],refresh-rate={hz}]]",
        ]
    if gpu and d["vnc"]:
        # The VNC binding brings its own tablet and keyboard for this screen.
        a += ["--vnc-server", f"host=127.0.0.1,port={d['vnc_port']},screen=gpu-0"]
    else:
        if gpu:
            a += ["--wayland-sock", wayland]
        if d["keyboard"]:
            a.append("--display-window-keyboard")
        if d["mouse"]:
            a.append("--display-window-mouse")
    a.append(str(vm / "firmware.fd"))
    env = components.library_environment(snapshot.runtime, "macos")
    if gpu:
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


# Explorer runs the first sign-in's script when it starts the desktop, which can
# be while Windows still shows its per-user setup pages (CloudExperienceHost, in
# wwahost.exe) over it. Drawn with the GPU driver those pages hang the host GPU
# (GitHub issue #1), so setup counts as finished only once they have closed.
SETUP_CHECK = (
    "if exist " + SETUP_MARKER + ' (tasklist /fi "imagename eq wwahost.exe" /nh'
    ' | find /i "wwahost.exe" >nul || echo GUNYAHVM_SETUP_COMPLETE)'
)


def setup_complete(vm, cfg, ip):
    """Whether Windows setup has finished: the first sign-in's script has run and
    no setup pages are open."""
    from . import windows_agent

    result = windows_agent.command(vm, cfg, ip, SETUP_CHECK)
    return result.returncode == 0 and "GUNYAHVM_SETUP_COMPLETE" in result.stdout


# For a VM created before its installation media carried the driver: its first
# sign-in wrote no marker, so a signed-in account other than the control
# account counts as finished setup.
INSTALL_SCRIPT = r"""$ErrorActionPreference = 'Stop'
$dir = 'C:\gunyahvm\wingpu'
$marker = 'C:\gunyahvm\setup-complete'
if (-not (Test-Path -LiteralPath $marker)) {
  $control = '__CONTROL_USER__'
  $people = Get-CimInstance Win32_UserProfile | Where-Object {
    -not $_.Special -and (Split-Path -Leaf $_.LocalPath) -notin @($control, 'defaultuser0')
  }
  if (-not $people) { throw 'Finish Windows setup and sign in once, then install the GPU driver' }
}
# Setup pages still open would come back with the GPU and hang it (see SETUP_CHECK).
if (Get-Process -Name wwahost -ErrorAction SilentlyContinue) {
  throw 'Finish the Windows setup pages that are still open, then install the GPU driver'
}
foreach ($store in @('Root', 'TrustedPublisher')) {
  certutil.exe -addstore -f $store (Join-Path $dir 'DroidVM-GPU-Test.cer') | Out-Null
  if ($LASTEXITCODE -ne 0) { throw "GPU driver certificate import into $store failed" }
}
$boot = (bcdedit.exe /enum '{current}') -join "`n"
if ($boot -notmatch 'testsigning\s+Yes') {
  bcdedit.exe /set '{current}' testsigning on | Out-Null
  if ($LASTEXITCODE -ne 0) { throw 'Could not enable test signing for the GPU driver' }
}
# The package ships with GunyahVM's own Vulkan driver in place of its original one, so
# the package's catalog no longer lists that file and pnputil refuses it (0xE000024B).
# Catalog the files again and sign that catalog with a certificate made here for this VM;
# its private key is deleted right after signing, so it can sign nothing else.
$driver = Join-Path $dir 'driver'
$catalog = Join-Path $driver 'virtio_wddm_rs.cat'
$fresh = Join-Path $dir 'virtio_wddm_rs.cat'
$signer = New-SelfSignedCertificate -Type CodeSigningCert -Subject 'CN=GunyahVM Windows GPU driver (this VM)' `
  -CertStoreLocation Cert:\LocalMachine\My -NotAfter (Get-Date).AddYears(20)
try {
  foreach ($store in @('Root', 'TrustedPublisher')) {
    $trust = New-Object System.Security.Cryptography.X509Certificates.X509Store($store, 'LocalMachine')
    $trust.Open('ReadWrite')
    $trust.Add([System.Security.Cryptography.X509Certificates.X509Certificate2]::new($signer.RawData))
    $trust.Close()
  }
  Remove-Item -LiteralPath $catalog, $fresh -Force -ErrorAction SilentlyContinue
  New-FileCatalog -Path $driver -CatalogFilePath $fresh -CatalogVersion 2.0 | Out-Null
  $signed = Set-AuthenticodeSignature -FilePath $fresh -Certificate $signer -HashAlgorithm SHA256
  if ($signed.Status -ne 'Valid') { throw "Could not sign the GPU driver catalog: $($signed.StatusMessage)" }
  Move-Item -LiteralPath $fresh -Destination $catalog
} finally {
  Remove-Item -Path ('Cert:\LocalMachine\My\' + $signer.Thumbprint) -DeleteKey
}
pnputil.exe /add-driver (Join-Path $driver 'virtio_wddm_rs.inf') /install | Out-Null
# 259: staged, no matching device yet; the GPU appears from the next start.
if ($LASTEXITCODE -notin 0, 259, 3010) { throw "pnputil failed with $LASTEXITCODE" }
New-Item -ItemType File -Force -Path $marker | Out-Null
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
    body = INSTALL_SCRIPT.replace("__CONTROL_USER__", cfg["windows"]["ssh_user"])
    script = base64.b64encode(body.encode("utf-16le")).decode("ascii")
    installed = subprocess.run(
        ssh + ["powershell -NoProfile -ExecutionPolicy Bypass -EncodedCommand " + script],
        capture_output=True, text=True, timeout=180)
    if installed.returncode or "GUNYAHVM_GPU_DRIVER_STAGED" not in installed.stdout:
        raise ValueError("Windows could not install the GPU driver: "
                         + (installed.stderr or installed.stdout).strip()[-800:])
    return component["version"]
