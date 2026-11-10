# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The creation dialog offers Windows only with its runtime, and keeps its fields apart."""

from pathlib import Path

import pytest


def runtime(windows):
    features = {
        "crosvm": ["gunyah", "simplefb", "gpu", "vnc"],
        "renderer": ["virgl"],
        "guest_kernel": ["virtio-gpu"],
    }
    if windows:
        features.update(uefi=["windows-ecam"], windows_drivers=[], windows_openssh=["offline"])
    return {"components": {k: {"version": "1", "features": v} for k, v in features.items()}}


@pytest.mark.parametrize("windows", [False, True])
def test_windows_choice_follows_feature_gate(windows, tmp_path):
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    if not Gtk.init_check():
        pytest.skip("GTK display required")
    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 9):
        pytest.skip("the frontend needs libadwaita 1.9")
    Adw.init()
    from gunyahvm import capabilities
    from gunyahvm.ui.wizard import CreateDialog

    parent = Adw.Window()
    parent.snapshot = capabilities.Snapshot(runtime=runtime(windows))
    parent.values, parent.pending, parent.store = {}, {}, Path(tmp_path)
    dialog = CreateDialog(parent)
    try:
        assert ("windows11" in dialog.choices) is windows
        for kind in ("cloud", "linux"):
            dialog.open(kind)
            assert not hasattr(dialog.details, "password")
            assert dialog.details.gpu.keys[0] == "none"
            dialog.nav.pop()
        if windows:
            dialog.open("windows11")
            details = dialog.details
            assert details.user.get_visible() and details.password.get_visible()
            assert details.gpu is None
            assert not details.create_button.get_sensitive()
    finally:
        dialog.force_close()
        parent.destroy()
