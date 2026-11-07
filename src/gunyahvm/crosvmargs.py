# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""crosvm backend: validated model + resolved payload -> argv and environment."""

from pathlib import Path
from . import config, components


def vnc_arg(cfg, pwfile=None):
    d = cfg["display"]
    return ["--vnc-server", f"host=127.0.0.1,port={d['vnc_port']}"] if d["vnc"] else []


def gpu_args(cfg, wayland_sock, feats, vnc_pwfile=None):
    d = cfg["display"]
    mode = d["gpu"]
    if mode == "none":
        return [], {}
    if d.get("vnc"):
        wayland_sock = None
    disp = "displays=[[mode=windowed[%d,%d]%s]]" % (
        d["width"],
        d["height"],
        (",dpi=[%d,%d]" % (d["dpi"], d["dpi"])) if d["dpi"] else "",
    )
    env = {}
    if d.get("mouse", True):
        env["CROSVM_GVM_INPUT_MODE"] = d.get("input_mode", "tablet")
    if d.get("fullscreen"):
        env["CROSVM_GVM_FULLSCREEN"] = "1"
        if d.get("output"):
            env["CROSVM_GVM_OUTPUT"] = d["output"]
    if mode == "simplefb":
        args = ["--simplefb", "width=%d,height=%d" % (d["width"], d["height"])]
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
    snapshot.require(cfg)
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
    if not shared:
        a += ["--swiotlb", "256" if cfg["os"] == "windows11" else "128"]
    a += ["--block", str(vm / "disk.raw")]
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
        if share["ro"]:
            raise ValueError(
                "This crosvm 9p backend has no read-only export; select a writable share or remove it"
            )
        # Same uid in crosvm and virtio-9p. Never root idmaps or privileged binds.
        a += [
            "--shared-dir",
            f"{path}:{share['tag']}:type=p9:uidmap=0 {cfg['uid']} 1:gidmap=0 {cfg['gid']} 1",
        ]
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
    g, env = gpu_args(cfg, wayland, {"guest_accept": accept})
    a += g
    # The graphics-enabled binary links libvirglrenderer even in headless mode.
    if "renderer" in runtime["components"]:
        renderer = components.payload(runtime, "renderer", "library")
        env["LD_LIBRARY_PATH"] = str(renderer.parent)
        server = runtime["components"]["renderer"]["files"].get("render_server")
        if server:
            env["RENDER_SERVER_EXEC_PATH"] = server
            if cfg["display"]["gpu"] == "venus":
                a += ["--gpu-render-server", "path=" + server]
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
