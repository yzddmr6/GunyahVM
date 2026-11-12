# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""All graphical guest profiles open the same application display."""

import sys
from types import SimpleNamespace

import pytest

from gunyahvm import cli
from gunyahvm.errors import Failure


@pytest.mark.parametrize("guest", ["linux", "windows11"])
def test_view_opens_application_without_vnc(monkeypatch, tmp_path, guest):
    cfg = {"os": guest, "display": {"vnc": False}}
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(cli.core, "owned_vm", lambda *a, **k: (tmp_path, cfg))
    monkeypatch.setattr(cli.core, "read_json", lambda p: {"pid": 123})
    monkeypatch.setattr(cli.core, "vmm_active", lambda s: True)
    called = []
    monkeypatch.setitem(sys.modules, "gunyahvm.ui.app", SimpleNamespace(
        main=lambda store, view=None: called.append((store, view)) or 0))
    assert cli.main(["--store", str(tmp_path), "view", "guest"]) == 0
    assert called == [(tmp_path, "guest")]


def test_view_rejects_stopped_guest(monkeypatch, tmp_path):
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(cli.core, "owned_vm", lambda *a, **k: (tmp_path, {}))
    monkeypatch.setattr(cli.core, "read_json", lambda p: {})
    monkeypatch.setattr(cli.core, "vmm_active", lambda s: False)
    with pytest.raises(Failure, match="running VM"):
        cli.main(["--store", str(tmp_path), "view", "guest"])


@pytest.mark.parametrize("gpu,vnc", [("none", False), ("simplefb", True)])
def test_cli_headless_and_explicit_vnc_keep_their_backends(monkeypatch, tmp_path, gpu, vnc):
    calls = []
    monkeypatch.setattr(cli.core, "start_vm", lambda *args, **kw: calls.append((args, kw)))
    cli.start_from_cli(tmp_path, "guest", {"os": "linux", "display": {"gpu": gpu, "vnc": vnc}})
    assert calls == [((tmp_path, "guest"), {"display_socket": None})]


@pytest.mark.parametrize("guest,vnc", [("linux", False), ("windows11", False), ("windows11", True)])
def test_graphical_cli_uses_the_primary_app_socket(monkeypatch, tmp_path, guest, vnc):
    from gunyahvm import storage
    import socket
    endpoint = tmp_path / "display"
    monkeypatch.setattr(storage, "display_socket", lambda _: endpoint)
    argv = []
    calls = []
    app = SimpleNamespace(run=lambda args: argv.append(args) or 0)
    gio = SimpleNamespace(Application=lambda **_: app, ApplicationFlags=SimpleNamespace(
        IS_LAUNCHER=1, HANDLES_COMMAND_LINE=2))
    monkeypatch.setitem(sys.modules, "gi.repository", SimpleNamespace(Gio=gio))
    monkeypatch.setattr(cli.core, "restart_vm", lambda *args, **kw: calls.append((args, kw)))
    cfg = {"os": guest, "display": {"gpu": "simplefb", "vnc": vnc}, "id": "test"}
    with pytest.raises(Failure, match="did not create"):
        cli.start_from_cli(tmp_path, "guest", cfg, restart=True)
    assert not calls
    with socket.socket(socket.AF_UNIX) as server:
        server.bind(str(endpoint))
        cli.start_from_cli(tmp_path, "guest", cfg, restart=True)
    assert argv[-1] == ["gunyahvm", "--store", str(tmp_path), "--prepare-display", "guest"]
    assert calls == [((tmp_path, "guest"), {"display_socket": endpoint})]
