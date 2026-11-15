# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Format and key derivation follow blacktop/ipsw pkg/aea (MIT), reimplemented.
"""Decrypt an Apple Encrypted Archive (profile 1) from an official IPSW.

The archive key is wrapped for Apple's public FCS key (HPKE, RFC 9180,
DHKEM(P-256, HKDF-SHA256)/HKDF-SHA256/AES-256-GCM). Every root header,
cluster header and segment is authenticated before it is used; LZFSE
segments are decoded by the distribution's liblzfse.
"""

import base64
import ctypes
import ctypes.util
import hashlib
import hmac
import json
import os
import struct
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .macos_inputs import AppleRedirect, Cancelled, official

PROFILE_SYMMETRIC = 1
CHECKSUM_BYTES = {0: 0, 1: 8, 2: 32}
ZERO_BLOCK = 64 * 1024


class Invalid(ValueError):
    pass


def _extract(salt, ikm):
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def _expand(prk, info, length):
    out, block, counter = b"", b"", 1
    while len(out) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        out += block
        counter += 1
    return out[:length]


def _hkdf(ikm, salt, info, length):
    return _expand(_extract(salt or b"", ikm), info, length)


_KEM = b"KEM" + struct.pack(">H", 0x10)
_SUITE = b"HPKE" + struct.pack(">HHH", 0x10, 1, 2)


def _labeled_extract(suite, salt, label, ikm):
    return _extract(salt, b"HPKE-v1" + suite + label + ikm)


def _labeled_expand(suite, prk, label, info, length):
    return _expand(prk, struct.pack(">H", length) + b"HPKE-v1" + suite + label + info, length)


def hpke_open(private_key, enc, ciphertext):
    """RFC 9180 base mode, empty info and AAD, first message."""
    ephemeral = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), enc)
    shared_dh = private_key.exchange(ec.ECDH(), ephemeral)
    recipient = private_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    shared = _labeled_expand(_KEM, _labeled_extract(_KEM, b"", b"eae_prk", shared_dh),
                             b"shared_secret", enc + recipient, 32)
    context = (b"\0" + _labeled_extract(_SUITE, b"", b"psk_id_hash", b"")
               + _labeled_extract(_SUITE, b"", b"info_hash", b""))
    secret = _labeled_extract(_SUITE, shared, b"secret", b"")
    key = _labeled_expand(_SUITE, secret, b"key", context, 32)
    nonce = _labeled_expand(_SUITE, secret, b"base_nonce", context, 12)
    return AESGCM(key).decrypt(nonce, ciphertext, b"")


def _ctr(key, iv, data):
    return Cipher(algorithms.AES(key[:32]), modes.CTR(iv)).decryptor().update(data)


def _mac(key, data, salt=b""):
    mac = hmac.new(key, salt, hashlib.sha256)
    mac.update(data)
    mac.update(struct.pack("<Q", len(salt)))
    return mac.digest()


class _Lzfse:
    def __init__(self):
        name = ctypes.util.find_library("lzfse") or "liblzfse.so.1"
        try:
            lib = ctypes.CDLL(name)
        except OSError as exc:
            raise Invalid("liblzfse is not installed (package liblzfse1)") from exc
        self.decode = lib.lzfse_decode_buffer
        self.decode.restype = ctypes.c_size_t
        self.decode.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_char_p,
                                ctypes.c_size_t, ctypes.c_void_p]
        self.scratch_size = lib.lzfse_decode_scratch_size
        self.scratch_size.restype = ctypes.c_size_t
        self.local = threading.local()

    def __call__(self, data, size):
        local = self.local
        if getattr(local, "out", None) is None or len(local.out) < size + 1:
            local.out = ctypes.create_string_buffer(size + 1)
            local.scratch = ctypes.create_string_buffer(self.scratch_size())
        n = self.decode(local.out, size + 1, data, len(data), local.scratch)
        if n != size:
            raise Invalid(f"LZFSE segment decoded to {n} bytes, expected {size}")
        return local.out.raw[:size]


def metadata(path):
    with open(path, "rb") as stream:
        magic, profile, size = struct.unpack("<4sII", stream.read(12))
        if magic != b"AEA1":
            raise Invalid("not an Apple Encrypted Archive")
        if size > 1 << 20:
            raise Invalid("AEA metadata too large")
        auth = stream.read(size)
    values, at = {}, 0
    while at < len(auth):
        (length,) = struct.unpack_from("<I", auth, at)
        if length < 5 or at + length > len(auth):
            raise Invalid("invalid AEA metadata entry")
        key, sep, value = auth[at + 4:at + length].partition(b"\0")
        if not sep:
            raise Invalid("invalid AEA metadata entry")
        values[key.decode()] = value
        at += length
    return profile, auth, values


def fetch_key(values, opener=None):
    """Unwrap the archive key with Apple's published FCS private key."""
    url = official(values["com.apple.wkms.fcs-key-url"].decode())
    response = json.loads(values["com.apple.wkms.fcs-response"])
    opener = opener or urllib.request.build_opener(AppleRedirect()).open
    with opener(urllib.request.Request(url, headers={"Accept-Encoding": "identity"}),
                timeout=60) as reply:
        official(reply.geturl())
        pem = reply.read(1 << 16)
    key = serialization.load_pem_private_key(pem, None)
    if not isinstance(key, ec.EllipticCurvePrivateKey) or key.curve.name != "secp256r1":
        raise Invalid("unexpected FCS key type")
    return hpke_open(key, base64.b64decode(response["enc-request"]),
                     base64.b64decode(response["wrapped-key"]))


def decrypt(source, target, offset=0, key=None, progress=None, cancel=None, workers=None,
            opener=None):
    """Write the plain archive into TARGET at OFFSET; return (size, sha256).

    TARGET is opened without truncation, so a sparse store can receive the
    payload in its window. All-zero blocks are skipped to keep it sparse.
    """
    profile, auth, values = metadata(source)
    if profile & 0xFFFFFF != PROFILE_SYMMETRIC:
        raise Invalid("unsupported AEA profile")
    if key is None:
        key = fetch_key(values, opener)
    lzfse = _Lzfse()
    total_in = os.path.getsize(source)
    digest = hashlib.sha256()
    workers = workers or min(4, os.cpu_count() or 1)
    with open(source, "rb") as stream, \
            os.fdopen(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC, 0o600), "r+b") as out:
        stream.seek(12 + len(auth))
        salt = stream.read(32)
        main = _hkdf(key, salt, b"AEA_AMK" + struct.pack("<I", profile), 32)
        root_mac, root_data, cluster_mac = stream.read(32), stream.read(48), stream.read(32)
        rk = _hkdf(main, b"", b"AEA_RHEK", 80)
        mac = hmac.new(rk[:32], cluster_mac, hashlib.sha256)
        mac.update(auth)
        mac.update(root_data)
        mac.update(struct.pack("<Q", 32 + len(auth)))
        if not hmac.compare_digest(mac.digest(), root_mac):
            raise Invalid("AEA root header authentication failed")
        root = _ctr(rk[32:64], rk[64:80], root_data)
        size, encrypted, segment, per_cluster, compression, checksum = struct.unpack_from("<QQIIBB", root)
        if encrypted != total_in:
            raise Invalid("AEA archive is incomplete")
        if checksum not in CHECKSUM_BYTES or compression not in b"-e" or not 0 < segment <= 1 << 28 \
                or not 0 < per_cluster <= 1 << 18:
            raise Invalid("unsupported AEA layout")
        entry = 8 + CHECKSUM_BYTES[checksum]

        def segment_job(prk, index, data, plain_size, expected_mac, expected_sum):
            k = _expand(prk, b"AEA_SK" + struct.pack("<I", index), 80)
            if not hmac.compare_digest(_mac(k[:32], data), expected_mac):
                raise Invalid(f"AEA segment {index} authentication failed")
            plain = _ctr(k[32:64], k[64:80], data)
            if len(data) != plain_size:
                if compression != ord("e"):
                    raise Invalid("AEA segment compression mismatch")
                plain = lzfse(plain, plain_size)
            if checksum == 2 and hashlib.sha256(plain).digest() != expected_sum:
                raise Invalid(f"AEA segment {index} checksum mismatch")
            return plain

        done = 0
        with ThreadPoolExecutor(workers) as pool:
            cluster = 0
            while done < size:
                if cancel and cancel():
                    raise Cancelled()
                ck = _hkdf(main, b"", b"AEA_CK" + struct.pack("<I", cluster), 32)
                material = _hkdf(ck, b"", b"AEA_CHEK", 80)
                headers = stream.read(entry * per_cluster)
                macs = stream.read(32 + 32 * per_cluster)
                if len(macs) != 32 + 32 * per_cluster:
                    raise Invalid("truncated AEA cluster")
                if not hmac.compare_digest(_mac(material[:32], headers, macs), cluster_mac):
                    raise Invalid(f"AEA cluster {cluster} authentication failed")
                cluster_mac = macs[:32]
                headers = _ctr(material[32:64], material[64:80], headers)
                prk = _extract(b"", ck)
                jobs, queued = [], done
                for index in range(per_cluster):
                    plain_size, packed = struct.unpack_from("<II", headers, index * entry)
                    expected = min(segment, size - queued)
                    if plain_size != expected:
                        raise Invalid("AEA segment size mismatch")
                    if expected == 0:
                        if packed:
                            raise Invalid("nonempty unused AEA segment")
                        continue
                    if not 0 < packed <= segment:
                        raise Invalid("invalid AEA segment size")
                    data = stream.read(packed)
                    if len(data) != packed:
                        raise Invalid("truncated AEA segment")
                    checksum_bytes = headers[index * entry + 8:(index + 1) * entry]
                    future = pool.submit(segment_job, prk, index, data, plain_size,
                                         macs[32 + 32 * index:64 + 32 * index], checksum_bytes)
                    jobs.append((future, plain_size))
                    queued += plain_size
                    # Bound memory to a few segments per worker.
                    if len(jobs) >= workers * 2:
                        done = _drain(jobs[:1], out, offset, done, digest)
                        jobs = jobs[1:]
                done = _drain(jobs, out, offset, done, digest)
                if progress:
                    progress(done, size)
                cluster += 1
            padding = stream.read()
            pad_key = _hkdf(main, b"", b"AEA_PAK", 80)
            if not hmac.compare_digest(hmac.new(pad_key[:32], padding, hashlib.sha256).digest(),
                                       cluster_mac):
                raise Invalid("AEA padding authentication failed")
        end = offset + size
        out.seek(0, os.SEEK_END)
        if out.tell() < end:
            out.truncate(end)
        out.flush()
        os.fsync(out.fileno())
    return size, digest.hexdigest()


def _drain(jobs, out, offset, done, digest):
    for future, plain_size in jobs:
        plain = future.result()
        digest.update(plain)
        view = memoryview(plain)
        for at in range(0, len(plain), ZERO_BLOCK):
            end = min(at + ZERO_BLOCK, len(plain))
            if plain.count(0, at, end) != end - at:
                out.seek(offset + done + at)
                out.write(view[at:end])
        done += plain_size
    return done
