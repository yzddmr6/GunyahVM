# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""A prepared display follows the next run after an earlier forced stop."""

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("next_state", ["running", "failed"])
def test_prepared_display_waits_for_the_next_run(monkeypatch, tmp_path, next_state):
    pytest.importorskip("gi")
    from gunyahvm.ui import app as app_module

    previous = {"state": "failed", "run_dir": "runs/previous", "forced_stop": True}
    monkeypatch.setattr(app_module.core, "owned_vm", lambda *args, **kwargs: (tmp_path, {}))
    monkeypatch.setattr(app_module.core, "status", lambda *args: previous)
    shown, focused = [], []
    display = SimpleNamespace(focus=lambda: focused.append(True))
    window = SimpleNamespace(
        ensure_display=lambda *args: display,
        values={"test": {"status": previous}},
        displays={"test": display},
        content=SimpleNamespace(set_visible_child=lambda child: shown.append(child)),
        select=lambda *args, **kwargs: None,
    )
    app = app_module.App(tmp_path)
    app.activate = lambda: setattr(app, "win", window)
    window.reload = app.show_requested_display
    command = SimpleNamespace(
        get_arguments=lambda: ["gunyahvm", "--store", str(tmp_path), "--prepare-display", "test"],
        printerr_literal=lambda message: pytest.fail(message),
    )
    assert app.do_command_line(command) == 0
    assert shown == []
    window.values["test"]["status"] = {"state": next_state, "run_dir": "runs/next"}
    app.show_requested_display()
    assert app.prepared_display is None
    if next_state == "failed":
        # A later, independent start must not revive the failed start's request.
        window.values["test"]["status"] = {"state": "running", "run_dir": "runs/later"}
        app.show_requested_display()
    assert shown == ([display] if next_state == "running" else [])
    assert focused == ([True] if next_state == "running" else [])
