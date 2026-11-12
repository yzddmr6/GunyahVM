# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""A retained, unsupported VM is visible and read-only across an upgrade."""
import json
import os
from types import SimpleNamespace

import pytest

from gunyahvm import core
from gunyahvm.errors import Failure, RequiresUpdate


def test_retained_guest_in_mixed_library(tmp_path, monkeypatch):
    monkeypatch.setattr(core.pwd, "getpwuid", lambda _: SimpleNamespace(pw_dir=str(tmp_path)))
    store = tmp_path / "library"
    for name, owner in (("office", os.getuid()), ("other", os.getuid() + 1)):
        vm = store / "vms" / name
        vm.mkdir(parents=True)
        (vm / "config.json").write_text(json.dumps({
            "name": name, "uid": owner, "os": "macos", "schema": 52, "future": True,
        }))
    vm = store / "vms/office"
    disk = vm / "disk.raw"
    disk.write_bytes(b"preserve")
    config_bytes = (vm / "config.json").read_bytes()
    inode = disk.stat().st_ino
    values = core.list_vms(store)
    assert [(v["config"]["name"], v["status"]["state"]) for v in values] == [
        ("office", "requires-update"), ("other", "invalid"),
    ]
    for action in (core.start_vm_authorized, core.delete_vm):
        with pytest.raises(RequiresUpdate, match="newer version"):
            action(store, "office")
    with pytest.raises(Failure, match="identity"):
        core.owned_vm(store, "other", os.getuid())
    assert disk.stat().st_ino == inode and disk.read_bytes() == b"preserve"
    assert (vm / "config.json").read_bytes() == config_bytes
    assert {p.name for p in vm.iterdir()} == {"disk.raw", "config.json"}
