# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Pinned official Apple inputs. Downloading is separate from guest assembly."""
import hashlib
import os
from pathlib import Path
import stat
import shutil
import urllib.parse
import urllib.request

from . import storage

# Input identities used by the qualified native-APFS profile. No Apple bytes.
INPUTS = {
    '26B123': {
        'url': 'https://updates.cdn-apple.com/2026FallFCS/c9671975-52db-4fb5-9af2-00217169ba8d/UniversalMac_27.2_26B123_Restore.ipsw',
        'size': 25595669503,
        'sha256': '6613b34e8589db2202a01ec37a2648afb141ece13428175183f8f3e38438534f',
    },
    '26B8125': {
        'url': 'https://updates.cdn-apple.com/2026FallFCS/b83bb963-ebd8-4608-8b5d-fdc70cb24927/UniversalMac_27.2_26B8125_Restore.ipsw',
        'size': 20928022286,
        'sha256': 'b3f4cb057d73722af2a2840884778fd9fc5f433aa263d37767ebd44529b3c6f4',
    },
}
CHUNK = 8 * 1024 * 1024


def official(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != 'https' or parsed.username or parsed.password
            or parsed.port not in (None, 443)
            or not parsed.hostname
            or not (parsed.hostname == 'apple.com' or parsed.hostname.endswith('.apple.com')
                    or parsed.hostname == 'cdn-apple.com' or parsed.hostname.endswith('.cdn-apple.com'))):
        raise ValueError('macOS inputs must come directly from Apple over HTTPS')
    return url


class AppleRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def digest(path):
    with os.fdopen(storage.open_nofollow(path, os.O_RDONLY, os.getuid()), 'rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def fetch_file(spec, directory, open_url=None):
    """Resume a contiguous prefix; only an exact full hash can publish an IPSW."""
    official(spec['url'])
    directory = Path(directory)
    name = Path(urllib.parse.urlsplit(spec['url']).path).name
    if not name.endswith('.ipsw'):
        raise ValueError('Expected an IPSW input')
    final = directory / name
    part = directory / (name + '.part')
    if final.exists() or final.is_symlink():
        if final.stat().st_size != spec['size'] or digest(final) != spec['sha256']:
            raise ValueError('Existing official input failed verification')
        return final
    if not part.exists() and not part.is_symlink():
        fd = os.open(part, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    opener = open_url or urllib.request.build_opener(AppleRedirect()).open
    with os.fdopen(storage.open_nofollow(part, os.O_RDWR, os.getuid()), 'r+b') as out:
        offset = out.seek(0, os.SEEK_END)
        if shutil.disk_usage(directory).free < max(0, spec["size"] - offset) + CHUNK:
            raise ValueError("Not enough free space for this official input")
        if offset > spec['size']:
            raise ValueError('Partial input is larger than the pinned file; clean it first')
        while offset < spec['size']:
            end = min(offset + CHUNK, spec['size']) - 1
            req = urllib.request.Request(spec['url'], headers={
                'Range': f'bytes={offset}-{end}', 'Accept-Encoding': 'identity'})
            with opener(req, timeout=60) as response:
                official(response.geturl())
                if (response.status != 206 or response.headers.get('Content-Range')
                        != f'bytes {offset}-{end}/{spec["size"]}'):
                    raise ValueError('Apple server did not honor the exact download range')
                chunk = response.read(end - offset + 2)
                if len(chunk) != end - offset + 1:
                    raise ValueError('Incomplete download range; rerun to resume')
            out.write(chunk)
            out.flush()
            os.fsync(out.fileno())
            offset += len(chunk)
            print(f'{name}: {offset}/{spec["size"]}', flush=True)
    if digest(part) != spec['sha256']:
        raise ValueError('Official input digest mismatch; clean partials before retrying')
    # Keep a successfully verified destination intact even if another process
    # created it. The caller holds the directory operation lock.
    os.link(part, final, follow_symlinks=False)
    part.unlink()
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return final


def download(directory, clean=False):
    directory = Path(directory).expanduser()
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Use a private download directory owned by your account')
    with storage.operation_lock(directory):
        marker = directory / 'official-inputs.json'
        identity = {'schema': 1, 'inputs': INPUTS}
        if marker.exists():
            if storage.read_json(marker) != identity:
                raise ValueError('Download directory belongs to another input profile')
        elif any(p.name != '.operation.lock' for p in directory.iterdir()):
            raise ValueError('Choose an empty download directory')
        else:
            storage.atomic_json(marker, identity)
        if clean:
            for spec in INPUTS.values():
                name = Path(urllib.parse.urlsplit(spec['url']).path).name
                (directory / (name + '.part')).unlink(missing_ok=True)
            return []
        return [fetch_file(spec, directory) for spec in INPUTS.values()]
