# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Read-only serial console of one virtual machine, refreshed by the state monitor."""

from gi.repository import Adw, Gtk

from . import util
from .i18n import _


class ConsoleDialog(Adw.Dialog):
    def __init__(self, name):
        super().__init__(title=_("Console"), content_width=820, content_height=600)
        self.name = name
        self.text = None
        view = Adw.ToolbarView()
        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=_("Console"), subtitle=name))
        self.copy_button = util.icon_button("edit-copy-symbolic", _("Copy All"), self.copy_all)
        header.pack_end(self.copy_button)
        view.add_top_bar(header)
        self.stack = Gtk.Stack()
        self.empty = Adw.StatusPage(
            icon_name="utilities-terminal-symbolic",
            title=_("No Output Yet"),
            description=_("Serial output appears here while the virtual machine runs. To type into the console, run “gunyahvm console {name} --interactive” in a terminal.").format(name=name),
        )
        self.stack.add_named(self.empty, "empty")
        self.view = Gtk.TextView(
            editable=False, cursor_visible=False, monospace=True,
            wrap_mode=Gtk.WrapMode.WORD_CHAR,
            top_margin=12, bottom_margin=12, left_margin=12, right_margin=12,
        )
        self.scroll = Gtk.ScrolledWindow(vexpand=True, child=self.view)
        self.stack.add_named(self.scroll, "text")
        view.set_content(self.stack)
        self.set_child(view)
        self.set_text("", None)

    def set_text(self, value, _state):
        value = value or ""
        if value == self.text:
            return
        adjustment = self.scroll.get_vadjustment()
        at_end = (
            adjustment.get_value() + adjustment.get_page_size() >= adjustment.get_upper() - 24
        )
        self.text = value
        self.view.get_buffer().set_text(value)
        self.stack.set_visible_child_name("text" if value else "empty")
        self.copy_button.set_sensitive(bool(value))
        if at_end:
            buffer = self.view.get_buffer()
            self.view.scroll_to_mark(buffer.get_insert(), 0, False, 0, 1)
            buffer.place_cursor(buffer.get_end_iter())
            self.view.scroll_to_iter(buffer.get_end_iter(), 0, False, 0, 1)

    def copy_all(self):
        if self.text:
            util.copy(self, self.text)
