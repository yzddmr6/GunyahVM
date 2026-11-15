# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The public macOS runtime: reviewed host programs and the pinned boot kit.

The host input holds the three host-side ELF programs and their licence
notices. The boot kit holds GunyahVM's own guest kernel, kexts, Metal plugin,
loaders and firmware, pinned file by file in tools/macos.lock.json "kit".
Neither contains an Apple file: Apple's parts are downloaded on the device.
"""
import hashlib
from pathlib import Path

ROLES = {
    'binary': '/usr/lib/gunyahvm/macos/crosvm/crosvm',
    'vnc_library': '/usr/lib/gunyahvm/macos/crosvm/libvncserver.so.1',
    'renderer': '/usr/lib/gunyahvm/macos/renderer/libvirglrenderer.so.1',
}
NOTICES = 'usr/share/doc/gunyahvm-runtime/licenses/macos/'


def programs(lock):
    return {p for p in lock['input']['files'] if not p.startswith(NOTICES)}


def notices(lock):
    return sorted(p for p in lock['input']['files'] if p.startswith(NOTICES))


KIT = 'usr/lib/gunyahvm/macos/kit/'
KIT_ROLES = {'kit': '/' + KIT + 'kit.json', 'firmware': '/' + KIT + 'firmware.fd'}


def validate_kit(root, lock):
    spec = lock['kit']
    for relative, expected in spec['files'].items():
        file = Path(root) / KIT / relative
        if file.is_symlink() or not file.is_file():
            raise ValueError('Missing macOS boot kit file: ' + relative)
        with file.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != expected:
                raise ValueError('Unpinned macOS boot kit file: ' + relative)
    present = {str(p.relative_to(Path(root) / KIT)) for p in (Path(root) / KIT).rglob('*') if p.is_file()}
    if present - set(spec['files']) - {'kit.json'}:
        raise ValueError('Unpinned files in the macOS boot kit')


def validate(root, component, lock):
    files = dict(component['files'])
    if {k: files.pop(k) for k in KIT_ROLES if k in files} not in ({}, KIT_ROLES):
        raise ValueError('Invalid macOS boot kit roles')
    if 'kit' in component['files']:
        validate_kit(root, lock)
    if files != ROLES:
        raise ValueError('Public macOS runtime may contain only the reviewed host programs')
    if programs(lock) != {p.lstrip('/') for p in ROLES.values()}:
        raise ValueError('Public macOS input inventory contains unreviewed files')
    for path in ROLES.values():
        relative = path.lstrip('/')
        file = Path(root) / relative
        if file.is_symlink() or not file.is_file():
            raise ValueError('Invalid public macOS payload: ' + relative)
        with file.open('rb') as stream:
            if stream.read(4) != b'\x7fELF':
                raise ValueError('Public macOS host payload must be ELF')
            stream.seek(0)
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if digest != lock['input']['files'][relative]['sha256']:
            raise ValueError('Unreviewed public macOS payload: ' + relative)
    for relative in notices(lock):
        if '..' in Path(relative).parts:
            raise ValueError('Invalid macOS notice path: ' + relative)
        file = Path(root) / relative
        if file.is_symlink() or not file.is_file():
            raise ValueError('Missing macOS notice: ' + relative)
        data = file.read_bytes()
        if b'\0' in data or data[:4] in (b'\x7fELF', b'\xcf\xfa\xed\xfe', b'\xca\xfe\xba\xbe'):
            raise ValueError('macOS notices must be text: ' + relative)
        if hashlib.sha256(data).hexdigest() != lock['input']['files'][relative]['sha256']:
            raise ValueError('Unreviewed macOS notice: ' + relative)
