# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""A guest follows its window exactly when its VMM does, also before the window's first probe.

The settings say when a guest's resolution follows its window instead of the setting."""

from pathlib import Path
import sys
import types

import pytest
from test_capabilities import capable
from test_core_boundaries import vm_config


def adw():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk

    if not Gtk.init_check():
        pytest.skip("GTK display required")
    if (Adw.get_major_version(), Adw.get_minor_version()) < (1, 9):
        pytest.skip("the frontend needs libadwaita 1.9")
    Adw.init()
    return Adw


def host(follow):
    snapshot = capable()
    if follow:
        snapshot.runtime["components"]["crosvm"]["features"].append("follow-window")
    return snapshot


def linux_virgl():
    cfg = vm_config()
    cfg["guest_payload"] = True
    cfg["display"]["gpu"] = "virgl"
    return cfg


class Display:
    """The embedded viewer, without its compositor."""

    def __init__(self, vm_id, width, height):
        self.follow = None

    def queue_resize(self):
        pass


class Content:
    def add_named(self, child, name):
        pass


def new_window(monkeypatch, follow):
    """A window that has just opened: its own host probe has not returned yet."""
    adw()
    from gunyahvm import capabilities
    from gunyahvm.ui import app as app_module

    viewer = types.ModuleType("gunyahvm.ui.display")
    viewer.VmDisplay = Display
    monkeypatch.setitem(sys.modules, "gunyahvm.ui.display", viewer)
    monkeypatch.setattr(app_module.capabilities, "probe", lambda: host(follow))
    window = app_module.MainWindow.__new__(app_module.MainWindow)
    window.snapshot, window.probed = capabilities.Snapshot(), False
    window.host_ready, window.host_banners = True, []
    window.sync = window.reload = lambda: None
    window.displays, window.content = {}, Content()
    return app_module, window


@pytest.mark.parametrize("follow", [False, True])
def test_a_start_right_after_the_window_opens_follows_as_the_vmm_does(monkeypatch, follow):
    # The window shows at once and probes in the background; Start can come first. The VMM
    # decides from a probe of its own, so the viewer must not decide from the empty placeholder.
    _app, window = new_window(monkeypatch, follow)
    display = window.ensure_display("test", linux_virgl())
    assert window.probed and display.follow is follow


def test_the_command_line_opens_the_application_with_a_following_display(monkeypatch, tmp_path):
    # gunyahvm start with the application closed: the command line creates the window and asks
    # it for the display at once, before that window's probe has returned.
    app_module, window = new_window(monkeypatch, True)
    cfg = linux_virgl()
    monkeypatch.setattr(app_module.core, "owned_vm", lambda store, name, uid, inputs: (tmp_path, cfg))
    monkeypatch.setattr(app_module.core, "status", lambda vm, cfg: {"state": "stopped"})
    app = app_module.App(tmp_path)
    app.activate = lambda: setattr(app, "win", window)

    class CommandLine:
        errors = []

        def get_arguments(self):
            return ["gunyahvm", "--store", str(tmp_path), "--prepare-display", "test"]

        def printerr_literal(self, message):
            self.errors.append(message)

    line = CommandLine()
    assert app.do_command_line(line) == 0, line.errors
    assert app.prepared_display == ("test", None) and window.displays["test"].follow is True


def dialog_for(cfg, follow):
    Adw = adw()
    from gunyahvm.ui.settings import VmSettings

    snapshot = host(follow)
    parent = Adw.Window()
    parent.snapshot = snapshot
    parent.store = Path("/nonexistent")
    return VmSettings(parent, {"config": cfg, "status": {"state": "stopped"}})


@pytest.mark.parametrize("follow", [False, True])
def test_resolution_row_follows_the_window(follow):
    dialog = dialog_for(linux_virgl(), follow)
    assert dialog.resolution.get_sensitive() is not follow
    assert (dialog.resolution.get_subtitle() or "") == ("Follows the window size." if follow else "")


def test_windows_keeps_its_resolution_setting():
    cfg = vm_config(windows=True)
    cfg["display"]["gpu"] = "simplefb"
    dialog = dialog_for(cfg, True)
    assert dialog.resolution.get_sensitive()
    assert not dialog.resolution.get_subtitle()
