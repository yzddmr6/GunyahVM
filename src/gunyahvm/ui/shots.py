# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
from gi.repository import Graphene, Gtk


def save(widget, path):
    """Render widget to path; False when it has not been drawn yet (the caller retries)."""
    w, h = widget.get_width(), widget.get_height()
    if w <= 0 or h <= 0:
        return False
    snap = Gtk.Snapshot()
    Gtk.WidgetPaintable.new(widget).snapshot(snap, w, h)
    node = snap.to_node()
    if node is None:
        return False
    tex = (
        widget.get_native()
        .get_renderer()
        .render_texture(node, Graphene.Rect().init(0, 0, w, h))
    )
    tex.save_to_png(path)
    print("wrote", path)
    return True
