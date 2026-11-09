# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Masked platform DT discovery through the fixed read-only helper."""
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from gunyahvm import host, privilege, privileged

SUMMARY = {"hypervisor": True, "capability_ids": {"/hypervisor/rm": [17, 23]}}


def test_masked_live_tree_uses_summary_and_keeps_board(monkeypatch):
    monkeypatch.setattr(host, "read_tree", lambda root: {"/": {"compatible": b"oneplus,erhai\0qcom,sm8750\0"}})
    monkeypatch.setattr(host.os, "geteuid", lambda: 1001)
    monkeypatch.setattr(Path, "read_bytes", lambda path: (_ for _ in ()).throw(PermissionError("root only")))
    calls = []
    monkeypatch.setattr(privilege, "firmware_info", lambda: (calls.append(True), SUMMARY)[1])
    result = host.firmware().require()
    assert calls == [True]
    assert result.compatible == ["oneplus,erhai", "qcom,sm8750"]
    assert result.capability_ids == SUMMARY["capability_ids"]
    with pytest.raises(host.FirmwareUnavailable):
        host.firmware(Path("/caller/tree"), Path("/caller/fdt"))
    assert calls == [True]


def test_complete_live_tree_never_invokes_helper(monkeypatch):
    import struct
    nodes = {"/": {"compatible": b"xiaomi,piano\0"},
             "/hypervisor": {"compatible": b"gunyah-hypervisor\0"},
             "/hypervisor/rm": {"compatible": b"gunyah-resource-manager\0", "reg": struct.pack(">QQ", 17, 23)}}
    monkeypatch.setattr(host, "read_tree", lambda root: nodes)
    monkeypatch.setattr(privilege, "firmware_info", lambda: pytest.fail("unnecessary privilege boundary"))
    assert host.firmware().require().source == "live tree"


def test_helper_rejects_any_path_before_identifying_caller(monkeypatch):
    monkeypatch.setattr(privileged, "caller", lambda: pytest.fail("must reject extra arguments first"))
    with pytest.raises(ValueError, match="arguments"):
        privileged.main(["firmware-info", "/sys/firmware/fdt"])


def test_helper_exports_only_rm_summary(monkeypatch, capsys):
    monkeypatch.setattr(privileged, "caller", lambda: SimpleNamespace(pw_uid=1001))
    monkeypatch.setattr(host, "FDT", SimpleNamespace(read_bytes=lambda: b"raw"))
    monkeypatch.setattr(host, "parse_fdt", lambda data: {})
    monkeypatch.setattr(host, "firmware_from_nodes", lambda *args: host.Firmware(["private-board"], {"vmid": 42}, SUMMARY["capability_ids"], True, "raw", {"irq": [1]}))
    privileged.main(["firmware-info"])
    assert json.loads(capsys.readouterr().out) == SUMMARY


def test_client_has_no_paths_and_rejects_extra_summary_fields(monkeypatch):
    commands = []
    def run(argv, **kwargs):
        commands.append(argv)
        return SimpleNamespace(stdout=json.dumps(SUMMARY))
    monkeypatch.setattr(privilege.subprocess, "run", run)
    assert privilege.firmware_info() == SUMMARY
    assert commands == [["pkexec", "--disable-internal-agent", privilege.HELPER, "firmware-info"]]
    monkeypatch.setattr(privilege.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(SUMMARY | {"path": "/etc/shadow"})))
    with pytest.raises(OSError, match="summary fields"):
        privilege.firmware_info()
