# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Explicit clipboard transfers; never monitor or log either clipboard."""

import os
from gi.repository import Adw, Gtk

from .. import clipboard, core
from . import util
from .i18n import _


class ClipboardDialog(Adw.Dialog):
    def __init__(self, window):
        super().__init__(title=_("Text Clipboard"), content_width=480)
        self.window = window
        self.vm, self.cfg = core.owned_vm(window.store, window.selected, os.getuid(), inputs=False)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                      margin_start=20, margin_end=20, margin_top=16, margin_bottom=24)
        toolbar = Adw.ToolbarView(content=box)
        toolbar.add_top_bar(Adw.HeaderBar())
        self.set_child(toolbar)
        self.status = Gtk.Label(label=_("Checking guest clipboard…"), wrap=True)
        box.append(self.status)
        self.send = Gtk.Button(label=_("Send Host Text to Guest"), sensitive=False)
        self.receive = Gtk.Button(label=_("Copy Guest Text to Host"), sensitive=False)
        box.append(self.send)
        box.append(self.receive)
        box.append(Gtk.Label(label=_("Text is transferred only when you press a button. Maximum 1 MiB."), wrap=True))
        self.send.connect("clicked", self.send_text)
        self.receive.connect("clicked", lambda *_: self.work("read"))
        if self.cfg["os"] == "windows11":
            self.status.set_label(_("Unavailable: Windows needs a desktop clipboard agent."))
        else:
            box.append(Gtk.Label(label=_("Linux needs wl-clipboard in the logged-in Wayland account."), wrap=True))
            self.work("probe")

    def send_text(self, *_):
        self.send.set_sensitive(False)
        def ready(source, result):
            try:
                text = source.read_text_finish(result)
                if text is None:
                    raise ValueError(_("The host clipboard has no text."))
                self.work("write", text)
            except Exception as error:
                self.status.set_label(str(error))
                self.send.set_sensitive(True)
        self.window.get_clipboard().read_text_async(None, ready)

    def work(self, action, text=""):
        self.send.set_sensitive(False)
        self.receive.set_sensitive(False)
        app = self.window.get_application()
        app.hold()
        def done(result, error):
            app.release()
            if error:
                self.status.set_label(str(error))
            else:
                if action == "read":
                    self.window.get_clipboard().set(result)
                self.status.set_label(_("Available") if action == "probe" else _("Text copied."))
            # An unavailable probe cannot be mistaken for a working agent.
            enabled = not error or action != "probe"
            self.send.set_sensitive(enabled)
            self.receive.set_sensitive(enabled)
            return False
        util.in_thread(lambda: clipboard.transfer(self.vm, self.cfg, action, text), done)
