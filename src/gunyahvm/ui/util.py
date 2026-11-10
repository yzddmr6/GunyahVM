# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""Small GTK helpers shared by the windows."""

import os
import threading

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from . import text
from .i18n import _

CSS = """
.vm-tile {
  min-width: 96px;
  min-height: 96px;
  border-radius: 28px;
  background-color: color-mix(in srgb, currentColor 7%, transparent);
  color: color-mix(in srgb, currentColor 75%, transparent);
}
.vm-tile.running {
  background-color: color-mix(in srgb, var(--accent-bg-color) 16%, transparent);
  color: var(--accent-color);
}
.status-dot {
  min-width: 10px;
  min-height: 10px;
  border-radius: 5px;
  background-color: color-mix(in srgb, currentColor 30%, transparent);
}
.status-dot.running { background-color: var(--success-color); }
.status-dot.warning { background-color: var(--warning-color); }
.status-dot.error { background-color: var(--error-color); }
.notice-icon.warning { color: var(--warning-color); }
.notice-icon.error { color: var(--error-color); }
.notice-icon.accent { color: var(--accent-color); }
.check-icon.good { color: var(--success-color); }
.check-icon.bad { color: var(--error-color); }
.check-icon.unknown { color: color-mix(in srgb, currentColor 45%, transparent); }
.console-text { padding: 12px; }
"""


def install_css():
    provider = Gtk.CssProvider()
    provider.load_from_string(CSS)
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def in_thread(work, done=None):
    """work() in a thread; done(result, error) on the main loop."""

    def t():
        try:
            r, e = work(), None
        except Exception as ex:  # shown to the user
            r, e = None, str(ex) or ex.__class__.__name__
        if done:
            GLib.idle_add(done, r, e)

    threading.Thread(target=t, daemon=True).start()


def alert(parent, heading, body, responses, default=None, destructive=None, callback=None, extra=None):
    dialog = Adw.AlertDialog(heading=heading, body=body)
    for rid, label in responses:
        dialog.add_response(rid, label)
    dialog.set_close_response(responses[0][0])
    if default:
        dialog.set_default_response(default)
    if destructive:
        dialog.set_response_appearance(destructive, Adw.ResponseAppearance.DESTRUCTIVE)
    if extra is not None:
        dialog.set_extra_child(extra)
    if callback:
        dialog.connect("response", lambda _d, response: callback(response))
    dialog.present(parent)
    return dialog


def clean(message):
    message = (message or "").strip()
    return message[7:] if message.startswith("error: ") else message


def details(message):
    """A collapsed, selectable block with the original technical text."""
    label = Gtk.Label(
        label=clean(message)[-2000:], wrap=True, selectable=True, xalign=0,
        margin_top=6,
    )
    label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    label.add_css_class("monospace")
    label.add_css_class("caption")
    return Gtk.Expander(label=_("Details"), child=label)


def error(parent, heading, message):
    """Explain a failure in the user's language and keep the original under Details."""
    message = clean(message)
    friendly = text.explain(message)
    alert(
        parent, heading, friendly or _("Something went wrong."),
        responses=(("close", _("_Close")),),
        default="close",
        extra=details(message) if message else None,
    )


def copy(widget, value):
    provider = Gdk.ContentProvider.new_for_bytes(
        "text/plain;charset=utf-8", GLib.Bytes.new(value.encode())
    )
    widget.get_clipboard().set_content(provider)


def button(label=None, icon=None, css=(), tooltip=None, cb=None):
    b = Gtk.Button(valign=Gtk.Align.CENTER, use_underline=True)
    if icon and label:
        b.set_child(Adw.ButtonContent(icon_name=icon, label=label, use_underline=True))
    elif icon:
        b.set_icon_name(icon)
    else:
        b.set_label(label)
    for c in css:
        b.add_css_class(c)
    if tooltip:
        b.set_tooltip_text(tooltip)
    if cb:
        b.connect("clicked", lambda *_: cb())
    return b


def icon_button(icon, tooltip, cb):
    b = button(icon=icon, css=("flat",), tooltip=tooltip, cb=cb)
    b.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    return b


def combo(title, items, subtitle=None):
    """A ComboRow over (key, label) pairs; row.keys keeps the backend values."""
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new([label for _k, label in items]))
    row.keys = [k for k, _label in items]
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def combo_set_items(row, items):
    """Replace the choices only when they changed: a new model resets the selection."""
    keys, labels = [k for k, _label in items], [label for _k, label in items]
    model = row.get_model()
    shown = [model.get_string(i) for i in range(model.get_n_items())] if model else None
    if getattr(row, "keys", None) == keys and shown == labels:
        return
    row.keys = keys
    row.set_model(Gtk.StringList.new(labels))


def combo_key(row):
    index = row.get_selected()
    return row.keys[index] if 0 <= index < len(row.keys) else None


def combo_select(row, key):
    if key in row.keys:
        row.set_selected(row.keys.index(key))


def spin(title, lower, upper, step, value, subtitle=None):
    adjustment = Gtk.Adjustment(
        lower=lower, upper=upper, step_increment=step, page_increment=step * 4,
        value=min(max(value, lower), upper),
    )
    row = Adw.SpinRow(title=title, adjustment=adjustment, numeric=True)
    if subtitle:
        row.set_subtitle(subtitle)
    return row


def memory_items(values):
    return [(v, text.gigabytes(v)) for v in values]


def resource_limits(snapshot):
    """(max vCPUs, max guest MiB) offered by the frontend for this host."""
    from .. import host

    resources = snapshot.profile["resources"]
    online = len(snapshot.cpus) or os.cpu_count() or 1
    cpus = max(1, min(online, resources["max_cpus"]))
    try:
        total = snapshot.memory.get("MemTotal")
        budget = host.memory_budget(total) if total else host.memory_budget()
        memory = min(resources["max_memory_mib"], budget["guest_max_mib"])
    except (OSError, ValueError, KeyError):
        memory = resources["max_memory_mib"]
    return cpus, memory
