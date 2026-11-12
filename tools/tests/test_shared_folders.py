# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Guest permission mapping, runtime gates and managed fstab replacement."""

import importlib.util
from pathlib import Path
import pytest
from gunyahvm import config, crosvmargs
from test_core_boundaries import vm_config
from test_capabilities import capable


def test_virtiofs_needs_readonly_runtime_and_maps_selected_guest_account(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / "share").mkdir()
    cfg = vm_config()
    cfg["shares"] = [{"path": str(tmp_path / "share"), "tag": "files", "ro": True,
                      "backend": "virtiofs", "guest_uid": 501, "guest_gid": 20}]
    config.validate(cfg)
    snap = capable()
    with pytest.raises(ValueError, match="virtiofs"):
        snap.require_selection(cfg)
    snap.runtime["components"]["crosvm"]["features"] += ["virtiofs", "virtiofs-readonly"]
    (tmp_path / "boot").mkdir()
    (tmp_path / "boot/cmdline").write_text("root=LABEL=rootfs")
    argv, _ = crosvmargs.build(tmp_path, cfg, tmp_path / "run", snap)
    share = argv[argv.index("--shared-dir") + 1]
    assert ":type=fs:" in share and ":ro=true:" in share
    assert f":uidmap=501 {cfg['uid']} 1:" in share
    assert f":gidmap=20 {cfg['gid']} 1:" in share
    cfg["memory_mode"] = "shared"
    with pytest.raises(ValueError, match="sandbox"):
        snap.require_selection(cfg)


def test_guest_mounts_replace_only_the_managed_section(tmp_path):
    path = Path(config.__file__).with_name("guest-setup.py")
    spec = importlib.util.spec_from_file_location("guest_setup_shares", path)
    guest = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guest)
    fstab = tmp_path / "etc/fstab"
    fstab.parent.mkdir()
    original = "UUID=owner-root / ext4 defaults 0 1\n"
    fstab.write_text(original)
    guest.configure_shared_folders({"shared_folders": [{"tag": "docs", "backend": "virtiofs", "ro": True}]}, tmp_path)
    assert fstab.read_text().startswith(original)
    assert "docs /mnt/gunyahvm/docs virtiofs ro,nosuid,nodev,nofail,x-systemd.automount" in fstab.read_text()
    guest.configure_shared_folders({"shared_folders": []}, tmp_path)
    assert fstab.read_text() == original
    with pytest.raises(ValueError, match="Invalid"):
        guest.configure_shared_folders({"shared_folders": [{"tag": "../bad", "backend": "virtiofs", "ro": True}]}, tmp_path)
    assert fstab.read_text() == original
