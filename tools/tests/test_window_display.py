# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Linux guest frames reach the window without copies."""

import pytest
from test_capabilities import capable
from test_core_boundaries import vm_config
from gunyahvm import crosvmargs, storage

EMBEDDED = str(storage.display_socket("11111111-1111-4111-8111-111111111111"))


def linux(gpu, **display):
    cfg = vm_config()
    cfg["guest_payload"] = True
    cfg["display"].update(gpu=gpu, **display)
    return cfg


def build(tmp_path, cfg, wayland, runtime=None):
    snapshot = capable()
    if runtime is not None:
        snapshot.runtime = runtime
    vm = tmp_path / "vm"
    (vm / "boot").mkdir(parents=True, exist_ok=True)
    (vm / "boot/cmdline").write_text("root=UUID=test rw")
    return crosvmargs.build(vm, cfg, tmp_path / "run", snapshot, wayland)


@pytest.mark.parametrize("gpu", ["2d", "virgl", "venus", "native"])
def test_renderer_keeps_the_drivers_sync_defaults(tmp_path, gpu):
    # Measured on the OnePlus Pad 2 Pro: overriding freedreno's explicit-sync heuristic changed
    # neither 3D throughput nor the frames on screen, so the VMM keeps Mesa's defaults.
    _, env = build(tmp_path, linux(gpu), EMBEDDED)
    assert "disable_explicit_sync_heuristic" not in env
