# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Linux guests render at their window's size, and their frames reach it without copies."""

import hashlib
import json
from pathlib import Path

import pytest
from test_capabilities import capable
from test_core_boundaries import vm_config
from gunyahvm import crosvmargs, storage

REPO = Path(__file__).resolve().parents[2]
EMBEDDED = str(storage.display_socket("11111111-1111-4111-8111-111111111111"))
SESSION = "/run/user/1000/wayland-0"


def shipped_runtime():
    return json.loads((REPO / "tools/runtime.lock.json").read_text())["manifest"]


def linux(gpu, **display):
    cfg = vm_config()
    cfg["guest_payload"] = True
    cfg["display"].update(gpu=gpu, **display)
    return cfg


def follow_runtime():
    """The test manifest with a crosvm that follows its window."""
    runtime = capable().runtime
    runtime["components"]["crosvm"]["features"].append("follow-window")
    return runtime


def build(tmp_path, cfg, wayland, runtime=None):
    snapshot = capable()
    if runtime is not None:
        snapshot.runtime = runtime
    vm = tmp_path / "vm"
    (vm / "boot").mkdir(parents=True, exist_ok=True)
    (vm / "boot/cmdline").write_text("root=UUID=test rw")
    return crosvmargs.build(vm, cfg, tmp_path / "run", snapshot, wayland)


@pytest.mark.parametrize("gpu", ["2d", "virgl", "venus", "native"])
def test_virtio_gpu_linux_guests_follow_their_window(gpu):
    assert crosvmargs.follows_window(linux(gpu), True)
    assert not crosvmargs.follows_window(linux(gpu), False)
    assert not crosvmargs.follows_window(linux(gpu, vnc=True), True)


@pytest.mark.parametrize("gpu", ["none", "simplefb"])
def test_framebuffer_guests_keep_their_resolution(gpu):
    assert not crosvmargs.follows_window(linux(gpu), True)


def test_windows_keeps_its_resolution():
    cfg = vm_config(windows=True)
    cfg["display"]["gpu"] = "simplefb"
    assert not crosvmargs.follows_window(cfg, True)


def test_only_the_embedded_viewer_is_filled():
    assert crosvmargs.embedded_display(EMBEDDED)
    assert not crosvmargs.embedded_display(SESSION)
    assert not crosvmargs.embedded_display(None)


@pytest.mark.parametrize("wayland,mode", [(EMBEDDED, "fill"), (SESSION, "1")])
def test_runtime_with_the_feature_follows_the_window(tmp_path, wayland, mode):
    _, env = build(tmp_path, linux("virgl"), wayland, follow_runtime())
    assert env["CROSVM_GVM_FOLLOW_WINDOW"] == mode


def test_older_runtime_and_simplefb_keep_a_fixed_size(tmp_path):
    _, env = build(tmp_path, linux("virgl"), EMBEDDED)  # test manifest: no follow-window
    assert "CROSVM_GVM_FOLLOW_WINDOW" not in env
    _, env = build(tmp_path, linux("simplefb"), EMBEDDED, follow_runtime())
    assert "CROSVM_GVM_FOLLOW_WINDOW" not in env


def pool(args):
    return int(args[args.index("--swiotlb") + 1])


PANEL = 3392 * 2400 * 4  # OnePlus Pad 2 Pro; Xiaomi Pad 8 Pro is 3200x2136


def drm(tmp_path, *outputs):
    """A /sys/class/drm with (name, status, modes) connectors."""
    for name, status, modes in outputs:
        d = tmp_path / "drm" / name
        d.mkdir(parents=True)
        (d / "status").write_text(status + "\n")
        (d / "modes").write_text("".join(m + "\n" for m in modes))
    return tmp_path / "drm"


def test_largest_connected_display_sets_the_frame(tmp_path):
    root = drm(tmp_path, ("card0-DSI-1", "connected", ["3392x2400"]),
               ("card0-DP-1", "disconnected", []), ("card0-Writeback-1", "unknown", []))
    assert crosvmargs.largest_display_frame(root) == PANEL
    root = drm(tmp_path / "ext", ("card0-DSI-1", "connected", ["3200x2136"]),
               ("card0-DP-1", "connected", ["1920x1080", "3840x2160", "1920x1080i"]))
    assert crosvmargs.largest_display_frame(root) == 3840 * 2160 * 4
    assert crosvmargs.largest_display_frame(tmp_path / "none") == 0


@pytest.mark.parametrize("gpu", ["2d", "virgl", "native"])
def test_window_sized_guests_get_room_for_full_screen_frames(tmp_path, gpu, monkeypatch):
    monkeypatch.setattr(crosvmargs, "largest_display_frame", lambda: PANEL)
    cfg = linux(gpu)
    cfg["memory_mib"] = 5632
    args, _ = build(tmp_path, cfg, EMBEDDED, follow_runtime())
    frames = -(-PANEL * crosvmargs.FOLLOW_FRAMES // crosvmargs.MIB)
    assert pool(args) == frames + crosvmargs.POOL_MARGIN_MIB
    args, _ = build(tmp_path, cfg, EMBEDDED)  # older runtime: fixed resolution
    assert pool(args) == crosvmargs.POOL_MIB


def test_pool_follows_the_display_and_the_guest_memory():
    cfg = linux("virgl")
    cfg["memory_mib"] = 5632
    small = crosvmargs.swiotlb_mib(cfg, True, 1280 * 800 * 4)
    large = crosvmargs.swiotlb_mib(cfg, True, 3840 * 2160 * 4)
    assert crosvmargs.POOL_MIB <= small < large
    # without a connected display the configured resolution is the frame
    assert crosvmargs.swiotlb_mib(cfg, True, 0) == crosvmargs.swiotlb_mib(
        cfg, True, cfg["display"]["width"] * cfg["display"]["height"] * 4)
    for memory in (512, 1024, 2048):
        cfg["memory_mib"] = memory
        size = crosvmargs.swiotlb_mib(cfg, True, PANEL)
        assert size == max(crosvmargs.POOL_MIB, min(size, memory // 4))
        assert size <= max(crosvmargs.POOL_MIB, memory // 4)


def test_fixed_size_guests_keep_their_pool(tmp_path):
    cfg = vm_config(windows=True)
    cfg["display"]["gpu"] = "simplefb"
    cfg["display"].update(width=1280, height=800)
    # three 4,128,768-byte scanout buffers and the transfer margin stay under the 256 MiB floor
    assert crosvmargs.swiotlb_mib(cfg, False) == crosvmargs.WINDOWS_POOL_MIB == 256
    cfg["display"].update(width=7680, height=4320)
    assert crosvmargs.swiotlb_mib(cfg, False) == 380 + crosvmargs.WINDOWS_TRANSFER_MARGIN_MIB
    args, _ = build(tmp_path, linux("simplefb"), EMBEDDED, follow_runtime())
    assert pool(args) == crosvmargs.POOL_MIB


def test_packaged_guest_kernel_sets_up_pools_above_341_mib():
    """kmalloc caps the slot array at 4 MiB (341 MiB of pool) unless guest patch 0006 is in."""
    sources = json.loads((REPO / "third_party/runtime/sources.json").read_text())
    names = [Path(p["file"]).name for p in sources["patches"]["guest"]]
    assert any(n.startswith("0006-swiotlb-allocate-the-restricted-pool") for n in names)
    release = sources["guest_kernel"]["release"]
    assert int(release.rsplit("gvm", 1)[1]) >= 64
    manifest = shipped_runtime()["components"]["guest_kernel"]
    assert manifest["kernel_release"] == release


@pytest.mark.parametrize("gpu", ["2d", "virgl", "venus", "native"])
def test_renderer_keeps_the_drivers_sync_defaults(tmp_path, gpu):
    # Measured on the OnePlus Pad 2 Pro: overriding freedreno's explicit-sync heuristic changed
    # neither 3D throughput nor the frames on screen, so the VMM keeps Mesa's defaults.
    _, env = build(tmp_path, linux(gpu), EMBEDDED)
    assert "disable_explicit_sync_heuristic" not in env


def test_shipped_crosvm_carries_the_display_patches():
    manifest = shipped_runtime()
    assert "follow-window" in manifest["components"]["crosvm"]["features"]
    sources = json.loads((REPO / "third_party/runtime/sources.json").read_text())
    queue = {Path(p["file"]).name: p["sha256"] for p in sources["patches"]["crosvm_runtime"]}
    for name in ("0041-present-virgl-scanouts-zero-copy.patch", "0042-follow-the-window-size.patch"):
        patch = REPO / "third_party/crosvm/patches" / name
        assert queue[name] == hashlib.sha256(patch.read_bytes()).hexdigest()
    lock = json.loads((REPO / "tools/runtime.lock.json").read_text())
    built = sources["original_artifacts"]["bin/crosvm"]["sha256"]
    assert lock["input"]["files"]["crosvm"]["sha256"] == built
    assert lock["outputs"]["usr/lib/gunyahvm/crosvm/crosvm"] == built
