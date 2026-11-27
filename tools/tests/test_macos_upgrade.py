# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Exercise the real dpkg preinst without accessing the host installation."""
import json
import os
from pathlib import Path
import subprocess

import pytest
from gunyahvm import macos

REPO = Path(__file__).resolve().parents[2]


def preinst(root):
    return subprocess.run(['sh', str(REPO / 'debian/preinst'), 'upgrade', '1.2.0-1+macos2'],
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


def test_packaged_kit_firmware_is_used_before_an_upgrade_copy(tmp_path):
    kit = tmp_path / 'kit-firmware.fd'
    kit.write_bytes(b'kit firmware')
    vm = tmp_path / 'vm'
    vm.mkdir()
    assert macos.local_firmware(vm, {'firmware': str(kit)}) == kit


def test_public_runtime_rejects_firmware_role_without_the_kit(tmp_path):
    import macos_public
    with pytest.raises(ValueError, match='boot kit roles'):
        macos_public.validate(tmp_path, {'files': {'firmware': '/usr/lib/gunyahvm/macos/firmware/macos.fd'}}, {})


def test_public_runtime_accepts_only_the_pinned_kit(tmp_path):
    import hashlib
    import macos_public
    component, lock = {"files": dict(macos_public.KIT_ROLES)}, {}
    kit = tmp_path / macos_public.KIT
    kit.mkdir(parents=True)
    (kit / 'kit.json').write_text('{}')
    (kit / 'firmware.fd').write_bytes(b'firmware')
    lock['kit'] = {'files': {'firmware.fd': hashlib.sha256(b'firmware').hexdigest()}}
    component['files'].update(macos_public.KIT_ROLES)
    macos_public.validate(tmp_path, component, lock)
    (kit / 'extra.dylib').write_bytes(b'unpinned')
    with pytest.raises(ValueError, match='Unpinned'):
        macos_public.validate(tmp_path, component, lock)
    (kit / 'extra.dylib').unlink()
    (kit / 'firmware.fd').write_bytes(b'changed')
    with pytest.raises(ValueError, match='Unpinned'):
        macos_public.validate(tmp_path, component, lock)


def test_legacy_idle_flag_is_removed_only_from_run_volume(tmp_path):
    source = tmp_path / 'original.img'
    run = tmp_path / 'run'
    run.mkdir()
    entry = '::/EFI/UniXNU/unixnu.toml'
    with source.open('wb') as image:
        image.truncate(4 << 20)
    subprocess.run(['mformat', '-i', source, '::'], check=True)
    subprocess.run(['mmd', '-i', source, '::/EFI', '::/EFI/UniXNU'], check=True)
    original = b'[[entry]]\nid = "xnu"\nargs = "debug=1 wfi=0 custom=2"\n'
    subprocess.run(['mcopy', '-i', source, '-', entry], input=original, check=True)
    boot = macos.boot_image_for_run(source, run)
    assert subprocess.check_output(['mcopy', '-i', source, entry, '-']) == original
    assert subprocess.check_output(['mcopy', '-i', boot, entry, '-']) == original.replace(b' wfi=0', b'')
    assert macos.boot_image_for_run(boot, tmp_path) == boot


def test_the_stderr_copy_diagnostic_is_left_out_of_new_and_existing_volumes(tmp_path):
    # stderr-log=1 is a bring-up diagnostic of the kit's XNU patches: every process's
    # stdout and stderr goes to the kernel log, which nothing reads.
    kit = json.loads((REPO / 'tools/macos.lock.json').read_text())['kit']['boot']['bootargs']
    assert 'stderr-log=1' in kit.split()
    created = macos.boot_args(kit).split()
    assert 'stderr-log=1' not in created and 'wfi=0' not in created
    assert created == [token for token in kit.split() if token != 'stderr-log=1']
    source = tmp_path / 'volume.img'
    run = tmp_path / 'run'
    run.mkdir()
    entry = '::/EFI/UniXNU/unixnu.toml'
    with source.open('wb') as image:
        image.truncate(4 << 20)
    subprocess.run(['mformat', '-i', source, '::'], check=True)
    subprocess.run(['mmd', '-i', source, '::/EFI', '::/EFI/UniXNU'], check=True)
    original = f'[[entry]]\nid = "xnu"\nargs = "{kit}"\n'.encode()  # a volume made by 1.4.0
    subprocess.run(['mcopy', '-i', source, '-', entry], input=original, check=True)
    boot = macos.boot_image_for_run(source, run)
    assert boot != source and subprocess.check_output(['mcopy', '-i', source, entry, '-']) == original
    assert subprocess.check_output(['mcopy', '-i', boot, entry, '-']) == original.replace(b' stderr-log=1', b'')
