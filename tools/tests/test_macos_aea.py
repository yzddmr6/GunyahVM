# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""AEA profile 1 framing and HPKE key unwrap, against a test encoder."""
import ctypes
import ctypes.util
import hashlib
import hmac
import os
import struct

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from gunyahvm import macos_aea as aea


def lzfse_encoder():
    name = ctypes.util.find_library("lzfse")
    if not name:
        return None
    lib = ctypes.CDLL(name)
    lib.lzfse_encode_buffer.restype = ctypes.c_size_t
    lib.lzfse_encode_buffer.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p,
                                        ctypes.c_size_t, ctypes.c_void_p]

    def encode(data):
        out = ctypes.create_string_buffer(len(data) + 4096)
        n = lib.lzfse_encode_buffer(out, len(out), data, len(data), None)
        return out.raw[:n] if 0 < n < len(data) else data
    return encode


def encode(plain, key, segment=4096, per_cluster=4, compress=None, padding=b"\0" * 7):
    """Inverse of macos_aea.decrypt for profile 1 (test-only)."""
    profile = 1
    auth = struct.pack("<I", 4 + 5) + b"k\0val"
    salt = os.urandom(32)
    main = aea._hkdf(key, salt, b"AEA_AMK" + struct.pack("<I", profile), 32)
    entry = 8 + 32
    clusters = []
    chunk = segment * per_cluster
    for c, at in enumerate(range(0, len(plain), chunk)):
        ck = aea._hkdf(main, b"", b"AEA_CK" + struct.pack("<I", c), 32)
        material = aea._hkdf(ck, b"", b"AEA_CHEK", 80)
        prk = aea._extract(b"", ck)
        headers, macs, data = b"", b"", b""
        for i in range(per_cluster):
            piece = plain[at + i * segment:at + (i + 1) * segment]
            if not piece:
                headers += bytes(entry)
                macs += bytes(32)
                continue
            packed = compress(piece) if compress else piece
            k = aea._expand(prk, b"AEA_SK" + struct.pack("<I", i), 80)
            enc = aea._ctr(k[32:64], k[64:80], packed)
            headers += struct.pack("<II", len(piece), len(enc)) + hashlib.sha256(piece).digest()
            macs += aea._mac(k[:32], enc)
            data += enc
        clusters.append((material, aea._ctr(material[32:64], material[64:80], headers), macs, data))
    pad_key = aea._hkdf(main, b"", b"AEA_PAK", 80)
    following = hmac.new(pad_key[:32], padding, hashlib.sha256).digest()
    body = b""
    for material, headers, macs, data in reversed(clusters):
        macs = following + macs
        following = aea._mac(material[:32], headers, macs)
        body = headers + macs + data + body
    rk = aea._hkdf(main, b"", b"AEA_RHEK", 80)
    total = 12 + len(auth) + 32 + 32 + 48 + 32 + len(body) + len(padding)
    root = struct.pack("<QQIIBB", len(plain), total, segment, per_cluster,
                       ord("e") if compress else ord("-"), 2) + bytes(22)
    root_data = aea._ctr(rk[32:64], rk[64:80], root)
    mac = hmac.new(rk[:32], following + auth + root_data + struct.pack("<Q", 32 + len(auth)),
                   hashlib.sha256).digest()
    out = b"AEA1" + struct.pack("<II", profile, len(auth)) + auth + salt + mac + root_data \
        + following + body + padding
    assert len(out) == total
    return out


@pytest.mark.parametrize("compressed", [False, True])
def test_decrypt_roundtrip_sparse_offset(tmp_path, compressed):
    compress = lzfse_encoder() if compressed else None
    if compressed and compress is None:
        pytest.skip("liblzfse not installed")
    key = os.urandom(32)
    plain = (os.urandom(5000) + bytes(70000) + b"apfs" * 4000)
    (tmp_path / "in.aea").write_bytes(encode(plain, key, compress=compress))
    target = tmp_path / "store.img"
    target.write_bytes(b"H" * 100)
    size, digest = aea.decrypt(tmp_path / "in.aea", target, offset=4096, key=key, workers=2)
    assert size == len(plain) and digest == hashlib.sha256(plain).hexdigest()
    data = target.read_bytes()
    assert data[:100] == b"H" * 100 and data[4096:] == plain


def test_tampered_segment_is_rejected(tmp_path):
    key = os.urandom(32)
    blob = bytearray(encode(os.urandom(20000), key))
    blob[-200] ^= 1
    (tmp_path / "in.aea").write_bytes(blob)
    with pytest.raises(aea.Invalid, match="authentication"):
        aea.decrypt(tmp_path / "in.aea", tmp_path / "out", key=key)


def test_wrong_key_is_rejected(tmp_path):
    (tmp_path / "in.aea").write_bytes(encode(b"data" * 100, os.urandom(32)))
    with pytest.raises(aea.Invalid, match="root header"):
        aea.decrypt(tmp_path / "in.aea", tmp_path / "out", key=os.urandom(32))


def test_cancel_stops_between_clusters(tmp_path):
    key = os.urandom(32)
    (tmp_path / "in.aea").write_bytes(encode(os.urandom(100000), key))
    with pytest.raises(aea.Cancelled):
        aea.decrypt(tmp_path / "in.aea", tmp_path / "out", key=key, cancel=lambda: True)


def seal(public, message):
    """RFC 9180 base-mode sender for the test, mirroring the receiver."""
    ephemeral = ec.generate_private_key(ec.SECP256R1())
    enc = ephemeral.public_key().public_bytes(serialization.Encoding.X962,
                                              serialization.PublicFormat.UncompressedPoint)
    dh = ephemeral.exchange(ec.ECDH(), public)
    recipient = public.public_bytes(serialization.Encoding.X962,
                                    serialization.PublicFormat.UncompressedPoint)
    shared = aea._labeled_expand(aea._KEM, aea._labeled_extract(aea._KEM, b"", b"eae_prk", dh),
                                 b"shared_secret", enc + recipient, 32)
    context = (b"\0" + aea._labeled_extract(aea._SUITE, b"", b"psk_id_hash", b"")
               + aea._labeled_extract(aea._SUITE, b"", b"info_hash", b""))
    secret = aea._labeled_extract(aea._SUITE, shared, b"secret", b"")
    key = aea._labeled_expand(aea._SUITE, secret, b"key", context, 32)
    nonce = aea._labeled_expand(aea._SUITE, secret, b"base_nonce", context, 12)
    return enc, AESGCM(key).encrypt(nonce, message, b"")


def test_hpke_unwraps_the_archive_key():
    private = ec.generate_private_key(ec.SECP256R1())
    enc, wrapped = seal(private.public_key(), b"k" * 32)
    assert aea.hpke_open(private, enc, wrapped) == b"k" * 32


def test_key_must_come_from_apple():
    values = {"com.apple.wkms.fcs-key-url": b"https://example.com/key",
              "com.apple.wkms.fcs-response": b"{}"}
    with pytest.raises(ValueError, match="directly from Apple"):
        aea.fetch_key(values, opener=lambda *a, **k: None)
