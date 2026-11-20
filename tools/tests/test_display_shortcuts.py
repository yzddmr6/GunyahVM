# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""A guest's display takes the desktop's shortcuts only while it has keyboard focus."""

import pytest


def test_shortcuts_go_to_the_guest_while_its_display_has_focus():
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gdk, Gtk
    from gunyahvm.ui import util

    calls = []

    class Surface:
        __class__ = Gdk.Toplevel

        def inhibit_system_shortcuts(self, event):
            calls.append(("inhibit", event))

        def restore_system_shortcuts(self):
            calls.append(("restore",))

    class Native:
        def get_surface(self):
            return Surface()

    class Widget:
        controllers = []

        def get_native(self):
            return Native()

        def add_controller(self, controller):
            self.controllers.append(controller)

    widget = Widget()
    focus = util.guest_keyboard_shortcuts(widget)
    assert widget.controllers == [focus] and isinstance(focus, Gtk.EventControllerFocus)
    focus.emit("enter")
    focus.emit("leave")
    assert calls == [("inhibit", None), ("restore",)]
