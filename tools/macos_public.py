# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Validate the pinned macOS boot kit; host programs belong to the shared VMM."""
import hashlib
from pathlib import Path

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
    if component["files"] != KIT_ROLES:
        raise ValueError('Invalid macOS boot kit roles')
    validate_kit(root, lock)
