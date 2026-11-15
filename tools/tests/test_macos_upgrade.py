# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Exercise the real dpkg preinst without accessing the host installation."""
import os
from pathlib import Path
import subprocess

import pytest
from gunyahvm import macos

REPO = Path(__file__).resolve().parents[2]


def preinst(root):
    return subprocess.run(['sh', str(REPO / 'debian/runtime-preinst'), 'upgrade', '1.2.0-1+macos2'],
                          env={**os.environ, 'DPKG_ROOT': str(root)}, capture_output=True)


def test_upgrade_preserves_old_firmware_without_touching_library(tmp_path):
    old = tmp_path / 'usr/lib/gunyahvm/macos/firmware/macos.fd'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'local firmware')
    disk = tmp_path / 'home/owner/.local/share/gunyahvm/vms/macos/disk.raw'
    disk.parent.mkdir(parents=True)
    disk.write_bytes(b'user data')
    before = disk.stat()
    assert preinst(tmp_path).returncode == 0
    saved = tmp_path / 'var/lib/gunyahvm/local-macos/firmware.fd'
    assert saved.read_bytes() == old.read_bytes()
    assert preinst(tmp_path).returncode == 0  # interrupted upgrades are repeatable
    old.unlink()  # dpkg removing the old package's file
    assert preinst(tmp_path).returncode == 0
    assert saved.read_bytes() == b'local firmware'
    assert disk.stat().st_ino == before.st_ino
    assert disk.stat().st_mtime_ns == before.st_mtime_ns
    assert disk.read_bytes() == b'user data'


def test_upgrade_refuses_to_replace_distinct_local_firmware(tmp_path):
    old = tmp_path / 'usr/lib/gunyahvm/macos/firmware/macos.fd'
    old.parent.mkdir(parents=True)
    old.write_bytes(b'old')
    saved = tmp_path / 'var/lib/gunyahvm/local-macos/firmware.fd'
    saved.parent.mkdir(parents=True)
    saved.write_bytes(b'other')
    assert preinst(tmp_path).returncode != 0
    assert saved.read_bytes() == b'other'


def test_vm_firmware_wins_without_global_manifest_mutation(tmp_path):
    (tmp_path / 'firmware.fd').write_bytes(b'local input')
    files = {'firmware': '/not-used'}
    assert macos.local_firmware(tmp_path, files) == tmp_path / 'firmware.fd'
    assert files == {'firmware': '/not-used'}


def test_public_runtime_rejects_firmware_role(tmp_path):
    import macos_public
    with pytest.raises(ValueError, match='reviewed host'):
        macos_public.validate(tmp_path, {'files': {**macos_public.ROLES, 'firmware': '/usr/lib/gunyahvm/macos/firmware/macos.fd'}}, {})
