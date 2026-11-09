# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Check the creation wizard's visible text with real GTK widgets."""

import pytest


@pytest.mark.parametrize("windows", [False, True])
def test_wizard_windows_text_follows_feature_gate(windows):
    gi = pytest.importorskip("gi")
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gunyahvm.ui.wizard import CreateWizard
    from gi.repository import Adw, Gtk

    if not Gtk.init_check():
        pytest.skip("GTK display required")
    Adw.init()
    parent = Adw.Window()

    class Snapshot:
        def features(self):
            return {"windows": windows, "vhost_net": False}

    parent.snapshot = Snapshot()
    wizard = CreateWizard(parent)

    def visible_text(widget):
        if not widget.get_visible():
            return []
        texts = [widget.get_text()] if isinstance(widget, Gtk.Label) else []
        child = widget.get_first_child()
        while child is not None:
            texts.extend(visible_text(child))
            child = child.get_next_sibling()
        return texts

    try:
        model = wizard.kind.get_model()
        choices = [model.get_string(i) for i in range(model.get_n_items())]
        assert any("Windows" in choice for choice in choices) is windows
        for kind in ("cloud", "linux"):
            wizard.kind.set_selected(wizard.kind.keys.index(kind))
            text = "\n".join(visible_text(wizard.stack.get_child_by_name("source")))
            assert "Installation source" in text
            assert "Windows" not in text
            assert "RDP" not in text
            assert not wizard.edition.get_visible()
            assert not wizard.local_user.get_visible()
            assert not wizard.local_password.get_visible()
        if windows:
            wizard.kind.set_selected(wizard.kind.keys.index("windows11"))
            assert wizard.edition.get_visible()
            assert wizard.local_user.get_visible()
            assert wizard.local_password.get_visible()
            assert wizard.windows_note.get_visible()
    finally:
        wizard.destroy()
        parent.destroy()
