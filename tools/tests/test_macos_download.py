# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
import hashlib
import io
from pathlib import Path
import pytest
from gunyahvm import macos_download as m


def fixture():
    data = b'fixture, not Apple software'
    spec = {'url': 'https://updates.cdn-apple.com/test.ipsw', 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest()}
    return spec, data


class Response(io.BytesIO):
    status = 206
    def __init__(self, data, begin, end, size):
        super().__init__(data)
        self.headers = {'Content-Range': f'bytes {begin}-{end}/{size}'}
    def geturl(self):
        return 'https://updates.cdn-apple.com/test.ipsw'


def test_cancel_resume_and_verify(tmp_path, monkeypatch):
    spec, data = fixture()
    monkeypatch.setattr(m, 'CHUNK', 8)
    calls = []
    def get(req, timeout):
        begin, end = map(int, req.headers['Range'][6:].split('-'))
        calls.append(begin)
        if len(calls) == 2:
            raise KeyboardInterrupt()
        return Response(data[begin:end+1], begin, end, len(data))
    with pytest.raises(KeyboardInterrupt):
        m.fetch_file(spec, tmp_path, get)
    assert (tmp_path / 'test.ipsw.part').read_bytes() == data[:8]
    assert not (tmp_path / 'test.ipsw').exists()
    result = m.fetch_file(spec, tmp_path, get)
    assert result.read_bytes() == data
    assert calls[:3] == [0, 8, 8]
    assert not (tmp_path / 'test.ipsw.part').exists()


def test_hash_failure_never_publishes(tmp_path):
    spec, data = fixture()
    def get(req, timeout):
        return Response(b'x' * len(data), 0, len(data)-1, len(data))
    with pytest.raises(ValueError, match='digest mismatch'):
        m.fetch_file(spec, tmp_path, get)
    assert not (tmp_path / 'test.ipsw').exists()


@pytest.mark.parametrize('url', ['http://apple.com/a', 'https://apple.com.evil.test/a',
                               'https://user@apple.com/a', 'https://example.com/a'])
def test_external_redirect_rejected_before_following(url):
    with pytest.raises(ValueError, match='directly from Apple'):
        m.AppleRedirect().redirect_request(None, None, 302, '', {}, url)


def test_clean_only_owned_partial_names(tmp_path, monkeypatch):
    spec, data = fixture()
    monkeypatch.setattr(m, 'INPUTS', {'fixture': spec})
    m.download(tmp_path, clean=True)
    (tmp_path / 'test.ipsw.part').write_bytes(b'partial')
    (tmp_path / 'test.ipsw').write_bytes(data)
    unrelated = tmp_path / 'disk.raw';unrelated.write_bytes(b'owner')
    m.download(tmp_path, clean=True)
    assert not (tmp_path / 'test.ipsw.part').exists()
    assert (tmp_path / 'test.ipsw').read_bytes() == data
    assert unrelated.read_bytes() == b'owner'


def test_symlink_partial_is_not_followed(tmp_path):
    spec, _ = fixture()
    victim = tmp_path / 'disk.raw';victim.write_bytes(b'owner')
    (tmp_path / 'test.ipsw.part').symlink_to(victim)
    with pytest.raises(OSError):
        m.fetch_file(spec, tmp_path)
    assert victim.read_bytes() == b'owner'


def test_cli_cancel_exits_without_traceback(monkeypatch, tmp_path, capsys):
    from gunyahvm import cli
    monkeypatch.setattr(cli.os, 'geteuid', lambda: 1000)
    def cancel(*args):
        raise KeyboardInterrupt()
    monkeypatch.setattr(m, 'download', cancel)
    assert cli.main(['fetch-macos-inputs', str(tmp_path)]) == 130
    assert 'resume' in capsys.readouterr().err
