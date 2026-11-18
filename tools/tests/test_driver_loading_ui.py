# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Drivers that a start or creation loads never make the window look unusable."""

import time

import pytest


def gtk():
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


def runtime():
    return {"components": {"crosvm": {"version": "1", "features": ["gunyah"]}}}


def spin(condition):
    from gi.repository import GLib
    context = GLib.MainContext.default()
    deadline = time.monotonic() + 15
    while not condition() and time.monotonic() < deadline:
        context.iteration(False)
        time.sleep(0.01)
    assert condition()


class Action:
    def __init__(self):
        self.states = []

    def set_enabled(self, value):
        self.states.append(value)


def window(app_module, snapshot, probed=True):
    window = app_module.MainWindow.__new__(app_module.MainWindow)
    window.snapshot, window.host_ready, window.host_banners = snapshot, True, []
    window.probed = probed
    window.sync = lambda: None
    action = Action()
    window.lookup_action = lambda name: action
    return window, action


@pytest.mark.parametrize("device_access,loadable,ready", [
    (True, False, True), (False, True, True), (False, False, False)])
def test_window_counts_loadable_drivers_as_ready(device_access, loadable, ready):
    gtk()
    from gunyahvm import capabilities
    from gunyahvm.ui import app as app_module
    snapshot = capabilities.Snapshot(device_access=device_access, drivers_loadable=loadable,
                                     runtime=runtime())
    view, _ = window(app_module, capabilities.Snapshot())
    view.host_loaded(snapshot, None)
    assert view.host_ready is ready


@pytest.mark.parametrize("loadable", [True, False])
def test_new_vm_loads_drivers_once_before_the_dialog(monkeypatch, loadable):
    gtk()
    from gunyahvm import capabilities, privilege
    from gunyahvm.ui import app as app_module
    state = {"loaded": not loadable}
    loads, dialogs = [], []

    def probe():
        return capabilities.Snapshot(device_access=state["loaded"], protected_share=state["loaded"],
                                     drivers_loadable=not state["loaded"], runtime=runtime())

    def call(action, *args):
        loads.append(action)
        state["loaded"] = True

    class Dialog:
        def __init__(self, parent):
            # Graphics modes are read from the snapshot the dialog receives.
            dialogs.append(parent.snapshot)

        def present(self, parent):
            pass

    monkeypatch.setattr(app_module.capabilities, "probe", probe)
    monkeypatch.setattr(privilege, "call", call)
    monkeypatch.setattr(app_module, "CreateDialog", Dialog)
    view, action = window(app_module, probe())
    view.new_vm()
    spin(lambda: dialogs)
    assert loads == (["load"] if loadable else [])
    assert dialogs[0].device_access and dialogs[0].protected_share is True
    assert action.states == ([False, True] if loadable else [])
    view.new_vm()  # loaded now: the dialog opens without another load
    spin(lambda: len(dialogs) == 2)
    assert loads == (["load"] if loadable else [])


def test_a_dialog_opened_before_the_first_probe_gets_a_probed_snapshot(monkeypatch):
    # The window shows at once and probes in the background; "+" can come first.
    gtk()
    from gunyahvm import capabilities, privilege
    from gunyahvm.ui import app as app_module
    dialogs = []

    class Dialog:
        def __init__(self, parent):
            dialogs.append(parent.snapshot)

        def present(self, parent):
            pass

    monkeypatch.setattr(app_module.capabilities, "probe", lambda: capabilities.Snapshot(
        device_access=True, protected_share=True, runtime=runtime()))
    monkeypatch.setattr(privilege, "call", lambda *args: pytest.fail("drivers are loaded"))
    monkeypatch.setattr(app_module, "CreateDialog", Dialog)
    view, _action = window(app_module, capabilities.Snapshot(), probed=False)
    view.new_vm()
    spin(lambda: dialogs)
    assert dialogs[0].device_access and dialogs[0].protected_share is True
    assert dialogs[0].runtime == runtime() and view.probed


def labels(widget):
    from gi.repository import Adw, Gtk
    found, stack = [], [widget]
    while stack:
        item = stack.pop()
        if isinstance(item, (Gtk.Label, Gtk.Button)) and item.get_label():
            found.append(item.get_label())
        if isinstance(item, Adw.ExpanderRow):
            found.append(item.get_subtitle())
        child = item.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return found


@pytest.mark.parametrize("loadable", [True, False])
def test_system_check_keeps_load_drivers_and_describes_automatic_loading(monkeypatch, loadable):
    gtk()
    from gunyahvm import capabilities
    from gunyahvm.ui import hostinfo
    snapshot = capabilities.Snapshot(drivers_loadable=loadable, runtime=runtime())
    rows = [("modules", False, "missing: gunyah_rsc_mgr", "fix"),
            ("device", False, "/dev/gunyah", "fix")]
    monkeypatch.setattr(hostinfo.capabilities, "probe", lambda: snapshot)
    monkeypatch.setattr(hostinfo.capabilities, "diagnostic_rows", lambda snap: rows)

    class Window:
        def host_loaded(self, snapshot, error):
            pass

    dialog = hostinfo.SystemCheck(Window())
    spin(lambda: dialog.stack.get_visible_child_name() == "result")
    shown = labels(dialog.page)
    assert "_Load Drivers" in shown
    if loadable:
        assert "Ready for Virtual Machines" in shown
        assert "Not loaded yet. Starting or creating a virtual machine loads them." in shown
        assert "Appears when the drivers are loaded." in shown
    else:
        assert "Not Ready Yet" in shown
        assert "Not loaded. Use Load Drivers." in shown
