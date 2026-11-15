# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The macOS page asks for an account, hands it to creation and shows progress and pauses."""

from pathlib import Path

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


class Parent:
    def __init__(self, Adw, store):
        from gunyahvm import capabilities
        self.window = Adw.Window()
        self.window.snapshot = capabilities.Snapshot(runtime={"components": {
            "crosvm": {"version": "1", "features": ["gunyah"]},
            "macos": {"version": "1", "features": ["create-from-apple"]}}})
        self.window.values, self.window.pending, self.window.store = {}, {}, Path(store)
        self.window.names = lambda: sorted(set(self.window.values) | set(self.window.pending))
        self.created = []
        self.window.create_vm = lambda *args: self.created.append(args)


def test_macos_page_needs_a_valid_account_and_matching_passwords(tmp_path):
    Adw = gtk()
    from gunyahvm.ui.wizard import CreateDialog
    parent = Parent(Adw, tmp_path)
    dialog = CreateDialog(parent.window)
    try:
        dialog.open("macos")
        details = dialog.details
        assert details.name.get_text() == "macos"
        assert not details.create_button.get_sensitive()
        details.user.set_text("Bad Name")
        details.password.set_text("secret")
        details.confirm.set_text("secret")
        assert not details.create_button.get_sensitive()
        details.user.set_text("tester")
        details.confirm.set_text("other")
        assert not details.create_button.get_sensitive()
        details.confirm.set_text("secret")
        assert details.create_button.get_sensitive()
        details.disk.set_value(96)
        details.keep.set_active(True)
        details.create()
        assert parent.created == [("macos", "macos-download",
                                   {"account": "tester", "disk_gib": 96, "keep_downloads": True},
                                   "secret")]
        assert details.password.get_text() == "" and details.confirm.get_text() == ""
    finally:
        dialog.force_close()
        parent.window.destroy()


def test_creation_progress_pause_and_resume(tmp_path, monkeypatch):
    gtk()
    from gi.repository import GLib
    from gunyahvm import macos_create
    from gunyahvm.ui import app as app_module
    runs = []

    class FakeCreation:
        def __init__(self, store, name, account, password, disk_gib, progress, cancel, keep_downloads):
            runs.append((name, account, password, disk_gib, keep_downloads))
            self.progress, self.cancel = progress, cancel

        def run(self):
            import time
            self.progress("download", 0.25, "141-64992-076.dmg.aea 2600/10368 MiB")
            deadline = time.monotonic() + 20
            while not self.cancel() and time.monotonic() < deadline:
                time.sleep(0.02)
            raise macos_create.Cancelled()

    monkeypatch.setattr(macos_create, "Creation", FakeCreation)
    monkeypatch.setattr(macos_create, "saved_request",
                        lambda store, name: {"account": "tester", "disk_gib": 64, "keep_downloads": False})
    window = app_module.MainWindow.__new__(app_module.MainWindow)
    state = {"pages": []}
    window.pending, window.items, window.values = {}, {}, {}
    window.store, window.selected, window.failed_key = Path(tmp_path), None, None
    window.sync = lambda: None
    window.update_item = lambda name: None
    window.update_page = lambda: state["pages"].append(dict(window.pending.get("mac", {})))
    window.split = type("S", (), {"set_show_content": lambda self, v: None})()
    window.get_application = lambda: type("A", (), {"hold": lambda s: None, "release": lambda s: None})()
    window.toasts = type("T", (), {"add_toast": lambda s, t: None})()
    window.reload = lambda: None
    window.create_vm("mac", "macos-download", {"account": "tester", "disk_gib": 64,
                                                "keep_downloads": False}, "secret")
    context = GLib.MainContext.default()

    def spin(condition):
        import time
        deadline = time.monotonic() + 15
        while not condition() and time.monotonic() < deadline:
            context.iteration(False)
            time.sleep(0.01)
        assert condition()

    spin(lambda: window.pending["mac"].get("progress"))
    fraction, text = window.pending["mac"]["progress"]
    assert fraction == 0.25 and "2.5" in text and "10.1" in text
    window.pause_creation("mac")
    spin(lambda: window.pending["mac"].get("paused"))
    assert runs == [("mac", "tester", "secret", 64, False)]
    window.create_vm("mac", "macos-download", None, None)  # Resume: settings from the work dir
    spin(lambda: len(runs) == 2 and window.pending["mac"].get("progress"))
    assert runs[1] == ("mac", "tester", None, 64, False)
    window.pause_creation("mac")
    spin(lambda: window.pending["mac"].get("paused"))
