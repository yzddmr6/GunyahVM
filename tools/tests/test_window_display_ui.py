# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The settings say when a guest's resolution follows its window instead of the setting."""

from pathlib import Path

import pytest
from test_capabilities import capable
from test_core_boundaries import vm_config


def dialog_for(cfg, follow):
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    if not Gtk.init_check():
        pytest.skip("GTK display required")
    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 9):
        pytest.skip("the frontend needs libadwaita 1.9")
    Adw.init()
    from gunyahvm.ui.settings import VmSettings

    snapshot = capable()
    if follow:
        snapshot.runtime["components"]["crosvm"]["features"].append("follow-window")
    parent = Adw.Window()
    parent.snapshot = snapshot
    parent.store = Path("/nonexistent")
    return VmSettings(parent, {"config": cfg, "status": {"state": "stopped"}})


@pytest.mark.parametrize("follow", [False, True])
def test_resolution_row_follows_the_window(follow):
    cfg = vm_config()
    cfg["guest_payload"] = True
    cfg["display"]["gpu"] = "virgl"
    dialog = dialog_for(cfg, follow)
    assert dialog.resolution.get_sensitive() is not follow
    assert (dialog.resolution.get_subtitle() or "") == ("Follows the window size." if follow else "")


def test_windows_keeps_its_resolution_setting():
    cfg = vm_config(windows=True)
    cfg["display"]["gpu"] = "simplefb"
    dialog = dialog_for(cfg, True)
    assert dialog.resolution.get_sensitive()
    assert not dialog.resolution.get_subtitle()
