# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Linux guests render at their window's size, and their frames reach it without copies."""

import pytest
from test_capabilities import capable
from test_core_boundaries import vm_config
from gunyahvm import crosvmargs, storage

EMBEDDED = str(storage.display_socket("11111111-1111-4111-8111-111111111111"))
SESSION = "/run/user/1000/wayland-0"


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


@pytest.mark.parametrize("gpu", ["2d", "virgl", "venus", "native"])
def test_renderer_keeps_the_drivers_sync_defaults(tmp_path, gpu):
    # Measured on the OnePlus Pad 2 Pro: overriding freedreno's explicit-sync heuristic changed
    # neither 3D throughput nor the frames on screen, so the VMM keeps Mesa's defaults.
    _, env = build(tmp_path, linux(gpu), EMBEDDED)
    assert "disable_explicit_sync_heuristic" not in env
