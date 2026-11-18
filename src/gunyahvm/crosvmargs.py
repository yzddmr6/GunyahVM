# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""crosvm backend: validated model + resolved payload -> argv and environment."""

from pathlib import Path
from . import config, components


LIBRARY_ROOTS = ("/lib", "/lib64", "/usr/lib", "/usr/lib64")
GL_LIBRARIES = ("libEGL.so.1", "libgbm.so.1")


def ld_so_dirs(root=Path("/")):
    """Library directories in the host's ld.so.conf, in ldconfig's order."""
    found = []

    def read(path):
        try:
            lines = path.read_text().splitlines()
        except OSError:
            return
        for line in lines:
            words = line.split("#", 1)[0].split()
            if words[:1] == ["include"] and len(words) == 2:
                pattern = root / words[1].lstrip("/")
                for included in sorted(pattern.parent.glob(pattern.name)):
                    read(included)
            elif len(words) == 1 and words[0].startswith("/") and words[0] not in found:
                found.append(words[0])

    read(root / "etc/ld.so.conf")
    return found


def host_gl_dirs(root=Path("/")):
    """Host GPU driver directories outside the standard library directories.

    Some systems ship their GPU driver under /opt and list it in ld.so.conf. crosvm binds the
    directories in its LD_LIBRARY_PATH into the GPU sandbox, so passing these keeps the sandboxed
    renderer on the host's driver instead of a software fallback.
    """
    dirs = []
    for directory in ld_so_dirs(root):
        if any(directory == r or directory.startswith(r + "/") for r in LIBRARY_ROOTS):
            continue
        if any((root / directory.lstrip("/") / name).exists() for name in GL_LIBRARIES):
            dirs.append(directory)
    return dirs


FOLLOW_MODES = ("2d", "virgl", "venus", "native")


def follows_window(cfg, supported):
    """Whether the guest display takes the size of its window.

    The Linux guest's virtio-gpu driver re-reads the display size when crosvm signals a change,
    and GNOME switches to the new preferred mode. simplefb guests (firmware, Windows) and macOS
    keep their configured resolution and are scaled to fit instead. `supported` says whether the
    runtime's crosvm has the follow-window feature; older runtimes keep a fixed resolution.
    """
    d = cfg["display"]
    return (supported and cfg["os"] == "linux" and d["gpu"] in FOLLOW_MODES
            and not d.get("vnc"))


# A protected guest's devices reach the host only through its restricted DMA pool, carved out of
# the guest's RAM. The Linux guest's virtio-gpu keeps the backing of every graphics resource mapped
# in the pool for the resource's lifetime: the compositor's scanout buffers, each client's window
# buffers, textures. A guest that renders at its window's size therefore needs room for frames of
# the largest display its window can fill; when the pool runs out, GNOME Shell cannot allocate its
# next scanout buffer and the guest's screen stops updating.
# During a mode switch every full-screen client holds buffers of the old and the new size: GNOME
# Shell, its desktop-icons window and two full-screen applications come to about 32 frames of four
# buffers each. Measured with a full-screen GunyahVM window on a 3392x2400 panel (2752x2308 guest
# pixels, 24.2 MiB frames), GNOME with two full-screen virgl GL clients through repeated resizes
# peaked at POOL_MARGIN_MIB (block, network and console bounce buffers, small textures) plus 21
# frames, and at 93% occupancy a 256 KiB mapping already failed for want of contiguous slots. The
# pool takes FOLLOW_FRAMES frames of the largest connected display (whose frame bounds the guest's:
# the window keeps its sidebar and header bar), plus the margin: 33 guest frames on that panel.
POOL_MIB = 128  # fixed-size guests: the 1280x800 default since 1.0
POOL_MARGIN_MIB = 64
FOLLOW_FRAMES = 24
# Windows: its GPU driver takes one pool for the whole run at boot and keeps DWM's three scanout
# buffers (each rounded up to 64 KiB) and each rendering context's transfer memory in it; its
# resolution stays fixed. WINDOWS_TRANSFER_MARGIN_MIB is a margin for that transfer memory, not a
# measurement (the pool's occupancy cannot be read). For the 1280x800 mode the formula gives
# 76 MiB; the pool keeps the 256 MiB Windows guests have run with until the transfer memory is
# measured.
WINDOWS_SCANOUT_BUFFERS = 3
WINDOWS_TRANSFER_MARGIN_MIB = 64
WINDOWS_POOL_MIB = 256
MIB = 1 << 20


def largest_display_frame(root=Path("/sys/class/drm")):
    """Bytes of an XRGB8888 frame in the largest mode of the connected outputs (the panel and any
    external monitor), from the kernel's DRM connectors; 0 when none is connected."""
    largest = 0
    for status in root.glob("card*-*/status"):
        try:
            if status.read_text().strip() != "connected":
                continue
            modes = (status.parent / "modes").read_text().split()
        except OSError:
            continue
        for mode in modes:
            width, _, height = mode.partition("x")
            if width.isdigit() and height.rstrip("i").isdigit():
                largest = max(largest, int(width) * int(height.rstrip("i")) * 4)
    return largest


def swiotlb_mib(cfg, follow, display_frame=None):
    """Restricted DMA pool size in MiB.

    A guest that follows its window gets FOLLOW_FRAMES frames of the largest connected display
    plus the margin, at most a quarter of its RAM: each bounced buffer also keeps its own pages in
    guest RAM, so this leaves the guest at least half of it. Windows gets its scanout buffers at
    its fixed resolution plus the transfer margin, at least WINDOWS_POOL_MIB.
    """
    d = cfg["display"]
    if cfg["os"] == "windows11":
        scanout = -(-d["width"] * d["height"] * 4 // (64 << 10)) * (64 << 10)
        need = -(-scanout * WINDOWS_SCANOUT_BUFFERS // MIB) + WINDOWS_TRANSFER_MARGIN_MIB
        return max(WINDOWS_POOL_MIB, need)
    if not follow:
        return POOL_MIB
    if display_frame is None:
        display_frame = largest_display_frame()
    frame = max(display_frame, d["width"] * d["height"] * 4)
    need = -(-frame * FOLLOW_FRAMES // MIB) + POOL_MARGIN_MIB
    return max(POOL_MIB, min(need, cfg["memory_mib"] // 4))


def embedded_display(wayland_sock):
    """The socket of GunyahVM's own viewer (storage.display_socket), not a desktop session."""
    return bool(wayland_sock) and Path(wayland_sock).parent.name.startswith("gunyahvm-display-")


def vnc_arg(cfg, pwfile=None):
    d = cfg["display"]
    return ["--vnc-server", f"host=127.0.0.1,port={d['vnc_port']}"] if d["vnc"] else []


def display_refresh(output=""):
    """Use Mutter's active output timing, including user-selected panel modes."""
    try:
        from gi.repository import Gio, GLib
    except ImportError:
        return 120
    service = "org.gnome.Mutter.DisplayConfig"
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        state = bus.call_sync(service, "/org/gnome/Mutter/DisplayConfig", service,
                              "GetCurrentState", None, None, Gio.DBusCallFlags.NO_AUTO_START,
                              1000, None).unpack()
        primary = next((m[5][0][0] for m in state[2] if m[4]), "")
        for spec, modes, _ in state[1]:
            if spec[0] == (output or primary):
                for mode in modes:
                    if mode[-1].get("is-current") and mode[3] >= 1:
                        return round(mode[3])
    except GLib.Error:
        pass
    return 120  # Product panel's default operating mode.


def gpu_args(cfg, wayland_sock, feats, vnc_pwfile=None):
    d = cfg["display"]
    mode = d["gpu"]
    if mode == "none":
        return [], {}
    if d.get("vnc"):
        wayland_sock = None
    hz = display_refresh(d.get("output", ""))
    disp = "displays=[[mode=windowed[%d,%d],refresh-rate=%d%s]]" % (
        d["width"],
        d["height"],
        hz,
        (",dpi=[%d,%d]" % (d["dpi"], d["dpi"])) if d["dpi"] else "",
    )
    env = {}
    if feats.get("follow_window") and wayland_sock:
        # The embedded viewer has no window frame to resize, so the guest's window asks to fill
        # it; in a desktop session the window follows whatever size the user gives it.
        env["CROSVM_GVM_FOLLOW_WINDOW"] = "fill" if embedded_display(wayland_sock) else "1"
    if d.get("mouse", True):
        env["CROSVM_GVM_INPUT_MODE"] = d.get("input_mode", "tablet")
    if d.get("fullscreen"):
        env["CROSVM_GVM_FULLSCREEN"] = "1"
        if d.get("output"):
            env["CROSVM_GVM_OUTPUT"] = d["output"]
    if mode == "simplefb":
        args = ["--simplefb", f"width={d['width']},height={d['height']},poll-hz={hz}"]
        if wayland_sock:
            args += ["--wayland-sock", wayland_sock]
        return args + vnc_arg(cfg, vnc_pwfile), env
    if mode == "2d":
        gpu = "backend=2d," + disp
    else:
        ct = {
            "virgl": "virgl:virgl2",
            "native": "virgl:virgl2:drm",
            "venus": "virgl:virgl2:venus",
            "zink": "virgl:virgl2:venus",
        }[mode]
        gpu = (
            "backend=virglrenderer,context-types=%s,egl=true,gles=true,surfaceless=true,"
            "fixed-blob-mapping=false,pci-bar-size=0x40000000,%s" % (ct, disp)
        )
        if feats.get("guest_accept"):
            env["CROSVM_GVM_ACCEPT"] = "1"

    args = ["--gpu", gpu]

    if wayland_sock:
        args += ["--wayland-sock", wayland_sock]
    if d["keyboard"]:
        args.append("--display-window-keyboard")
        if d.get("keyboard_grab") and wayland_sock:
            env["CROSVM_GVM_KBD_GRAB"] = "1"
    if d["mouse"] or d.get("vnc"):
        args.append("--display-window-mouse")
    return args + vnc_arg(cfg, vnc_pwfile), env


def build(vm, cfg, rd, snapshot, wayland=None):
    config.validate(cfg)
    # Argument preview needs component compatibility, not privileged RM data.
    # supervise_run enforces live firmware, resources and guards before spawn.
    snapshot.require_selection(cfg)
    if cfg["os"] == "macos":
        from . import macos
        return macos.build(vm, cfg, rd, snapshot, wayland)
    runtime = snapshot.runtime
    c = components.features(runtime, "crosvm")
    exe = components.payload(runtime, "crosvm", "binary")
    if any(ch in str(path) for path in (vm, rd) for ch in ",:\n\0"):
        raise ValueError("VM storage paths may not contain crosvm parameter separators")
    shared = cfg["memory_mode"] == "shared"
    accept = (
        snapshot.features()["guest_accept"]
        and cfg["guest_payload"]
        and cfg["display"]["gpu"] in ("venus", "native")
    )
    a = [
        str(exe),
        "--no-syslog",
        "--log-level",
        "info",
        "--extended-status",
        "run",
        "--hypervisor",
        "gunyah",
        "--protected-vm-pseudo-unprotected"
        if shared
        else "--protected-vm-without-firmware",
        "--name",
        "gunyahvm-" + cfg["name"],
        "--cpus",
        str(cfg["cpus"]),
        "--mem",
        str(cfg["memory_mib"]),
        "--hugepages",
        "--no-pmu",
        "--no-smt",
        "--no-usb",
        "--socket",
        str(rd / "control.sock"),
    ]
    # Balloon has caused SIGBUS on Gunyah. Omit its disable switch on builds
    # without the feature (those builds also lack the switch).
    if "balloon" in c:
        a += ["--no-balloon"]
    if accept and cfg["display"]["gpu"] in ("virgl", "venus", "native") or shared:
        a += [
            "--disable-sandbox"
        ]  # GuestAccept currently needs one ordinary-user process.
    else:
        a += ["--pivot-root", "/usr/share/gunyahvm/crosvm/empty"]
    follow = follows_window(cfg, "follow-window" in c)
    if not shared:
        a += ["--swiotlb", str(swiotlb_mib(cfg, follow))]
    a += ["--block", str(vm / "disk.raw") + ",async-executor=uring,multiple-workers=true"]
    if cfg["os"] == "linux":
        a += ["--block", str(vm / "seed.iso") + ",ro=true"]
    elif cfg["install_media"]:
        for name in ("boot", "install", "drivers"):
            a += [
                "--block",
                str(vm / "install" / (name + ".raw"))
                + (",ro=true" if name == "drivers" else ""),
            ]
    if cfg["network_mode"] == "nat":
        a += [
            "--net",
            f"tap-name={cfg['tap']},mac={cfg['mac']}"
            + (",vhost-net" if cfg["vhost_net"] else ""),
        ]
    for share in cfg["shares"]:
        path = Path(share["path"]).expanduser().resolve(strict=True)
        if not path.is_dir():
            raise ValueError("shared folder is not a directory")
        backend = share.get("backend", "p9")
        if share["ro"] and backend != "virtiofs":
            raise ValueError(
                "This crosvm 9p backend has no read-only export; select a writable share or remove it"
            )
        if backend == "virtiofs":
            # Map the desktop account, not a privileged host identity. DAX is
            # not used with protected guest memory; the normal virtqueues and
            # the existing filesystem sandbox carry requests.
            options = (f"type=fs:cache=auto:dax=false:ro={str(share['ro']).lower()}:"
                       f"uidmap={share['guest_uid']} {cfg['uid']} 1:gidmap={share['guest_gid']} {cfg['gid']} 1:"
                       f"uid={share['guest_uid']}:gid={share['guest_gid']}")
        else:
            options = f"type=p9:uidmap=0 {cfg['uid']} 1:gidmap=0 {cfg['gid']} 1"
        a += ["--shared-dir", f"{path}:{share['tag']}:{options}"]
    serials = [("serial", "serial")] + (
        [("virtio-console", "agent")] if cfg["os"] == "linux" else []
    )
    for device, name in serials:
        a += [
            "--serial",
            f"type=file,hardware={device},num=1,path={rd}/{name}.log,input={rd}/{'console' if name == 'serial' else name}.in"
            + (",console,earlycon" if name == "serial" else ""),
        ]
    if cfg["display"]["gpu"] != "none" and not cfg["display"]["vnc"] and not wayland:
        raise ValueError("A Wayland session or VNC display is required")
    g, env = gpu_args(cfg, wayland, {"guest_accept": accept, "follow_window": follow})
    a += g
    # The graphics-enabled binary links libvirglrenderer even in headless mode.
    if "renderer" in runtime["components"]:
        env.update(components.library_environment(runtime))
        server = runtime["components"]["renderer"]["files"].get("render_server")
        if server:
            env["RENDER_SERVER_EXEC_PATH"] = server
            if cfg["display"]["gpu"] == "venus":
                a += ["--gpu-render-server", "path=" + server]
    if cfg["display"]["gpu"] in ("virgl", "venus", "native"):
        paths = [p for p in env.get("LD_LIBRARY_PATH", "").split(":") if p] + host_gl_dirs()
        if paths:
            env["LD_LIBRARY_PATH"] = ":".join(paths)
    if cfg["display"]["gpu"] in ("virgl", "venus", "native"):
        env["CROSVM_GVM_SCANOUT_READ"] = "ctx0"
        if any(n["driver"] == "msm" and n["accessible"] for n in snapshot.render_nodes):
            env["FD_MESA_DEBUG"] = "noubwc,notile"
    if cfg["display"]["gpu"] == "virgl":
        env["CROSVM_GVM_ACCEPT"] = "0"
    if cfg["os"] == "windows11":
        env["CROSVM_GVM_PCI_ECAM"] = "1"
        a += [str(vm / "firmware.fd")]
    else:
        boot = vm / "boot"
        if cfg["guest_payload"]:
            kernel = components.payload(runtime, "guest_kernel", "image")
            initrd = components.payload(runtime, "guest_kernel", "initrd")
        else:
            kernel, initrd = boot / "Image", boot / "initrd.img"
            if cfg["display"]["gpu"] in ("2d", "virgl", "venus", "native") or shared:
                raise ValueError(
                    "Selected graphics/memory mode requires the matching runtime guest payload"
                )
        a += [
            "--initrd",
            str(initrd),
            "--params",
            (boot / "cmdline").read_text().strip() + " console=ttyS0",
            str(kernel),
        ]
    return a, env
