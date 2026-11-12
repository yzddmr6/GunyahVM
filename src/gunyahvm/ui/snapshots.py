# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Stopped-guest disk snapshots and cloning through the shared core API."""

from datetime import datetime
import argparse

from gi.repository import Adw, Gtk

from .. import core, snapshots
from . import util
from .i18n import _


def perform(window, name, work, done=None):
    window.busy[name] = "disk"
    window.sync()
    app = window.get_application()
    app.hold()

    def finished(result, error):
        window.busy.pop(name, None)
        window.reload()
        window.sync()
        if error:
            util.error(window, _("Couldn't Complete the Disk Operation"), error)
        elif done:
            done(result)
        app.release()

    util.in_thread(work, finished)


def name_dialog(window, title, initial, response_label, callback):
    entry = Adw.EntryRow(title=_("Name"), text=initial)
    box = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    box.add_css_class("boxed-list")
    box.append(entry)
    dialog = util.alert(window, title, _("Use lowercase letters, numbers and hyphens."),
                        (("cancel", _("_Cancel")), ("save", response_label)),
                        default="cancel", extra=box,
                        callback=lambda response: callback(entry.get_text()) if response == "save" else None)

    def validate(*_args):
        try:
            core.vm_name(entry.get_text())
        except (ValueError, argparse.ArgumentTypeError):
            dialog.set_response_enabled("save", False)
        else:
            dialog.set_response_enabled("save", True)

    entry.connect("changed", validate)
    validate()


def clone_dialog(window):
    name = window.selected

    def clone(new_name):
        perform(window, name, lambda: snapshots.clone(window.store, name, new_name),
                lambda _result: window.toasts.add_toast(Adw.Toast(title=_("Virtual machine cloned"))))

    name_dialog(window, _("Clone Virtual Machine"), (name[:26] + "-copy"), _("_Clone"), clone)


class SnapshotDialog(Adw.Dialog):
    def __init__(self, window):
        super().__init__(title=_("Disk Snapshots"), content_width=560, content_height=500)
        self.window, self.name = window, window.selected
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.group = Adw.PreferencesGroup(
            title=_("Saved Disks"),
            description=_("Snapshots save the guest disk while the virtual machine is off. Shared host folders are excluded."),
        )
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                      margin_top=20, margin_bottom=20, margin_start=20, margin_end=20)
        box.append(self.group)
        self.take_button = util.button(label=_("_Take Snapshot…"), css=("suggested-action",), cb=self.take)
        box.append(self.take_button)
        view.set_content(Gtk.ScrolledWindow(child=box))
        self.set_child(view)
        self.rows = []
        self.refresh()

    def refresh(self):
        for row in self.rows:
            self.group.remove(row)
        self.rows = []
        try:
            values = snapshots.list_snapshots(self.window.store, self.name)
        except Exception as exc:
            util.error(self.window, _("Couldn't Read Snapshots"), str(exc))
            return
        for item in values:
            label = item["name"]
            row = Adw.ActionRow(title=label, subtitle=datetime.fromtimestamp(item["created_at"]).strftime("%Y-%m-%d %H:%M"))
            row.add_suffix(util.icon_button("document-revert-symbolic", _("Restore"), lambda s=label: self.restore(s)))
            row.add_suffix(util.icon_button("user-trash-symbolic", _("Remove"), lambda s=label: self.remove(s)))
            self.group.add(row)
            self.rows.append(row)

    def run(self, action, snapshot):
        self.group.set_sensitive(False)
        self.take_button.set_sensitive(False)

        def done(_result):
            self.refresh()
            self.window.toasts.add_toast(Adw.Toast(title=_("Disk operation completed")))

        def work():
            return action(self.window.store, self.name, snapshot)

        # Refresh sensitivity even when the worker reports an error.
        def finish(result, error):
            self.group.set_sensitive(True)
            self.take_button.set_sensitive(True)
            self.window.busy.pop(self.name, None)
            self.window.reload()
            self.window.sync()
            if error:
                util.error(self.window, _("Couldn't Complete the Disk Operation"), error)
            else:
                done(result)
            self.window.get_application().release()

        self.window.busy[self.name] = "disk"
        self.window.sync()
        self.window.get_application().hold()
        util.in_thread(work, finish)

    def take(self):
        name_dialog(self.window, _("Take Snapshot"), datetime.now().strftime("snapshot-%Y%m%d-%H%M"),
                    _("_Save"), lambda name: self.run(snapshots.take, name))

    def restore(self, snapshot):
        util.alert(self.window, _("Restore “{name}”?").format(name=snapshot),
                   _("This replaces the current guest disk. Changes made after the snapshot will be lost."),
                   (("cancel", _("_Cancel")), ("restore", _("_Restore"))), default="cancel", destructive="restore",
                   callback=lambda response: self.run(snapshots.restore, snapshot) if response == "restore" else None)

    def remove(self, snapshot):
        util.alert(self.window, _("Remove “{name}”?").format(name=snapshot),
                   _("The saved snapshot will be permanently removed. The current guest disk is kept."),
                   (("cancel", _("_Cancel")), ("remove", _("_Remove"))), default="cancel", destructive="remove",
                   callback=lambda response: self.run(snapshots.delete, snapshot) if response == "remove" else None)
