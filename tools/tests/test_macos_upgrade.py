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


def _public_tree(tmp_path, notice=b'MIT License\n'):
    import hashlib
    import macos_public
    files = {}
    for path in macos_public.ROLES.values():
        target = tmp_path / path.lstrip('/')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'\x7fELF' + path.encode())
        files[path.lstrip('/')] = {'sha256': hashlib.sha256(target.read_bytes()).hexdigest()}
    relative = macos_public.NOTICES + 'virglrenderer/COPYING'
    (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
    (tmp_path / relative).write_bytes(notice)
    files[relative] = {'sha256': hashlib.sha256(notice).hexdigest()}
    return macos_public, {'files': dict(macos_public.ROLES)}, {'input': {'files': files}}


def test_public_runtime_accepts_licence_notices(tmp_path):
    macos_public, component, lock = _public_tree(tmp_path)
    macos_public.validate(tmp_path, component, lock)
    assert macos_public.notices(lock) == [macos_public.NOTICES + 'virglrenderer/COPYING']


def test_public_runtime_rejects_binary_notice(tmp_path):
    macos_public, component, lock = _public_tree(tmp_path, b'\xcf\xfa\xed\xfe\0\0')
    with pytest.raises(ValueError, match='text'):
        macos_public.validate(tmp_path, component, lock)


def test_public_runtime_rejects_unlisted_program(tmp_path):
    macos_public, component, lock = _public_tree(tmp_path)
    lock['input']['files']['usr/share/gunyahvm/macos/libvulkan_freedreno.dylib'] = {'sha256': '0' * 64}
    with pytest.raises(ValueError, match='unreviewed files'):
        macos_public.validate(tmp_path, component, lock)
