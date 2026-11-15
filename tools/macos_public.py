# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The public macOS runtime contains only reviewed host-side ELF programs."""
import hashlib
from pathlib import Path

ROLES = {
    'binary': '/usr/lib/gunyahvm/macos/crosvm/crosvm',
    'vnc_library': '/usr/lib/gunyahvm/macos/crosvm/libvncserver.so.1',
    'renderer': '/usr/lib/gunyahvm/macos/renderer/libvirglrenderer.so.1',
}


def validate(root, component, lock):
    if component['files'] != ROLES:
        raise ValueError('Public macOS runtime may contain only the reviewed host programs')
    if set(lock['input']['files']) != {p.lstrip('/') for p in ROLES.values()}:
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
