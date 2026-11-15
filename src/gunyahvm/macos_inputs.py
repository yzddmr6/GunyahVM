# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Official Apple inputs for the macOS 27.2 profile, fetched member by member.

Only the IPSW members the assembler needs are downloaded from Apple's CDN with
HTTP range requests: about 13.3 GB instead of two whole restore images. A
download resumes from its partial file, can be cancelled at any point, and is
published only after its ZIP CRC-32 and the pinned SHA-256 of its content
match. No Apple bytes are part of GunyahVM; this module only names them.
"""

import hashlib
import json
import os
from pathlib import Path
import shutil
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib

IPSW_26B123 = ("https://updates.cdn-apple.com/2026FallFCS/c9671975-52db-4fb5-9af2-00217169ba8d/"
               "UniversalMac_27.2_26B123_Restore.ipsw")
IPSW_26B8125 = ("https://updates.cdn-apple.com/2026FallFCS/b83bb963-ebd8-4608-8b5d-fdc70cb24927/"
                "UniversalMac_27.2_26B8125_Restore.ipsw")
IPSW_SIZE = {IPSW_26B123: 25595669503, IPSW_26B8125: 20928022286}

# name: local file; member: ZIP entry; offset: its local header; method 0
# stored / 8 deflated; sha256: of the stored or inflated content.
MEMBERS = {
    "kernelcache": {
        "ipsw": IPSW_26B123, "member": "kernelcache.release.vma2", "offset": 25572310563,
        "method": 8, "compressed": 23190310, "size": 23222533, "crc32": 3929452997,
        "sha256": "d38b044b59e856754e573885d592979c26ec016a8406bf1dc344cf146dc82625",
    },
    "os-trustcache": {
        "ipsw": IPSW_26B8125, "member": "Firmware/141-64992-076.dmg.aea.trustcache",
        "offset": 20749150061, "method": 8, "compressed": 398326, "size": 443785,
        "crc32": 2361397382,
        "sha256": "843f7c3ffac7fb406f8391a7d784edbc5ec4fc93a91757ad6080c8f74eb05a20",
    },
    "system-trustcache": {
        "ipsw": IPSW_26B8125, "member": "Firmware/141-64957-077.dmg.aea.trustcache",
        "offset": 20673645177, "method": 8, "compressed": 3192, "size": 3503,
        "crc32": 3977062435,
        "sha256": "ee7eb9867f0ca0fa5323e45782e1602dba3477c3068662fb56ed9e09eb79ff1a",
    },
    "app-trustcache": {
        "ipsw": IPSW_26B8125, "member": "Firmware/141-64745-077.dmg.trustcache",
        "offset": 20673620163, "method": 8, "compressed": 1999, "size": 2135,
        "crc32": 2107822236,
        "sha256": "bfca1fdc46830dfbf6e049623d99832e80f23fee00e9b522f8f91c1c35905772",
    },
    "app-cryptex": {
        "ipsw": IPSW_26B8125, "member": "141-64745-077.dmg", "offset": 1107296307,
        "method": 8, "compressed": 32851292, "size": 54525952, "crc32": 3849482042,
        "sha256": "6ffff0128ef5164da123be3b52449750ace758580baa0c6c0a41d0ed3e3d99c5",
    },
    "system-cryptex.aea": {
        "ipsw": IPSW_26B8125, "member": "141-64957-077.dmg.aea", "offset": 1526172552,
        "method": 0, "compressed": 2348810240, "size": 2348810240, "crc32": 3917338459,
        "sha256": "1256a93b26d0814b42938dbb07931278032dcec6284028964fa6ff49fb637b00",
        "plain_size": 2801795072,
        "plain_sha256": "c51b93c1008d26a9725a1838cd77f4d1d485d94d3810ce41bb2fecde94e5b79c",
    },
    "os.aea": {
        "ipsw": IPSW_26B8125, "member": "141-64992-076.dmg.aea", "offset": 9787139885,
        "method": 0, "compressed": 10871635968, "size": 10871635968, "crc32": 805493190,
        "sha256": "ca7708f657110a6dd3a237c48f86afa04f817fa274a8e3b8894942d0193ec253",
        "plain_size": 13723762688,
        "plain_sha256": "5c4a8164410e9767c1520cb64cd834f68cb84887f31bbe789e086c4dc401b858",
    },
}
# The arm64 kernel collection inside the kernelcache IM4P, before relinking.
APPLE_KC_SHA256 = "01332067965f721b7e6984348510b717ff346d253d7639212dcd9f6c6161afaf"
STOCK_TRUST_SHA256 = "6a116e8681fb30487524e61cca8401a5a2c5203f2ba19d5f7997be68f0635999"
SYSTEM_VOLUME_UUID = "354D4F10-9A79-4243-BA00-60FAB89D7561"
CHUNK = 1 << 20
RETRIES = 8
WORKERS = 4
CHUNKS = Path(__file__).with_name("macos-chunks.json")


class Cancelled(Exception):
    """The caller asked to stop; partial downloads stay resumable."""


def official(url):
    parsed = urllib.parse.urlsplit(url)
    if (parsed.scheme != "https" or parsed.username or parsed.password
            or parsed.port not in (None, 443) or not parsed.hostname
            or not (parsed.hostname == "apple.com" or parsed.hostname.endswith(".apple.com")
                    or parsed.hostname == "cdn-apple.com"
                    or parsed.hostname.endswith(".cdn-apple.com"))):
        raise ValueError("macOS inputs must come directly from Apple over HTTPS")
    return url


class AppleRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        official(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def total_download(names=None):
    return sum(MEMBERS[n]["compressed"] for n in (names or MEMBERS))


def digest(path):
    with open(path, "rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _range(opener, url, start, end, total):
    request = urllib.request.Request(url, headers={
        "Range": f"bytes={start}-{end}", "Accept-Encoding": "identity"})
    response = opener(request, timeout=60)
    official(response.geturl())
    if response.status != 206 or response.headers.get("Content-Range") != f"bytes {start}-{end}/{total}":
        response.close()
        raise ValueError("Apple's server did not return the requested range")
    return response


def _data_start(opener, spec):
    head = _range(opener, spec["ipsw"], spec["offset"], spec["offset"] + 29, IPSW_SIZE[spec["ipsw"]])
    with head:
        local = head.read(30)
    if len(local) != 30 or local[:4] != b"PK\x03\x04":
        raise ValueError("The official IPSW no longer has the pinned layout")
    method, = struct.unpack_from("<H", local, 8)
    name_len, extra_len = struct.unpack_from("<HH", local, 26)
    if method != spec["method"]:
        raise ValueError("The official IPSW member changed")
    return spec["offset"] + 30 + name_len + extra_len


def _private(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as out:
        out.write(data)


def chunk_pins():
    data = json.loads(CHUNKS.read_text())
    return data["chunk"], data["members"]


def fetch_chunked(name, directory, progress=None, cancel=None, opener=None, sleep=time.sleep,
                  workers=WORKERS):
    """A large stored member in pinned 32 MiB chunks over several connections.

    Apple's CDN can throttle one connection to a fraction of the link; each
    chunk is verified against its pinned SHA-256 before it counts as done,
    so a resume or a retry fetches only missing or damaged chunks.
    """
    spec = MEMBERS[name]
    size, pins = chunk_pins()
    pins = pins[name]
    if len(pins) != (spec["compressed"] + size - 1) // size:
        raise ValueError("chunk pins do not cover " + name)
    directory = Path(directory)
    final = directory / name
    part = directory / (name + ".part")
    state_path = directory / (name + ".part.json")
    opener = opener or urllib.request.build_opener(AppleRedirect()).open
    state = {}
    if part.exists() and state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except ValueError:
            state = {}
    if state.get("member") != spec["member"] or state.get("chunk") != size:
        state = {}
    done = set(state.get("chunks", []))
    missing = [i for i in range(len(pins)) if i not in done]
    need = sum(min(size, spec["compressed"] - i * size) for i in missing)
    if shutil.disk_usage(directory).free < need + (64 << 20):
        raise ValueError(f"Not enough free space to download {name}: need {need >> 20} MiB")
    start = state.get("start") or _data_start(opener, spec)
    fd = os.open(part, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    lock = threading.Lock()
    queue = list(missing)
    errors = []
    stopping = threading.Event()
    stop = lambda: stopping.is_set() or bool(cancel and cancel())  # noqa: E731
    received = [sum(min(size, spec["compressed"] - i * size) for i in done)]

    def save():
        _private(state_path, json.dumps({"member": spec["member"], "start": start, "chunk": size,
                                         "chunks": sorted(done)}))

    def worker():
        failures = 0
        while True:
            if stop() or errors:
                return
            with lock:
                if not queue:
                    return
                index = queue.pop(0)
            first = index * size
            length = min(size, spec["compressed"] - first)
            buffer = bytearray()
            try:
                response = _range(opener, spec["ipsw"], start + first, start + first + length - 1,
                                  IPSW_SIZE[spec["ipsw"]])
                with response:
                    while len(buffer) < length:
                        if stop():
                            with lock:
                                queue.insert(0, index)
                                received[0] -= len(buffer)
                            return
                        piece = response.read(min(CHUNK, length - len(buffer)))
                        if not piece:
                            raise ConnectionError("connection closed")
                        buffer += piece
                        with lock:
                            received[0] += len(piece)
                        if progress:
                            progress(min(received[0], spec["compressed"]), spec["compressed"])
                if hashlib.sha256(buffer).hexdigest() != pins[index]:
                    raise ConnectionError(f"chunk {index} of {name} arrived damaged")
                os.pwrite(fd, buffer, first)
                with lock:
                    done.add(index)
                    save()
                failures = 0
            except (OSError, urllib.error.URLError, ConnectionError, ValueError) as exc:
                with lock:
                    received[0] -= len(buffer)
                    queue.append(index)
                failures += 1
                if failures > RETRIES:
                    errors.append(exc)
                    return
                sleep(min(30, 2 ** failures))

    try:
        os.ftruncate(fd, spec["compressed"])
        save()
        threads = [threading.Thread(target=worker, daemon=True) for _ in range(min(workers, len(missing)) or 1)]
        for thread in threads:
            thread.start()
        try:
            for thread in threads:
                while thread.is_alive():
                    thread.join(0.5)
        except BaseException:
            # Ctrl-C: let every worker finish its write before the file closes.
            stopping.set()
            for thread in threads:
                thread.join(120)
            raise
        os.fsync(fd)
    finally:
        os.close(fd)
    if cancel and cancel() and len(done) < len(pins):
        raise Cancelled()
    if errors:
        raise ValueError(f"Download of {name} keeps failing ({errors[0]}); try again later") from errors[0]
    if len(done) != len(pins):
        raise ValueError(f"Download of {name} is incomplete; try again")
    os.replace(part, final)
    state_path.unlink(missing_ok=True)
    return final


def fetch(name, directory, progress=None, cancel=None, opener=None, sleep=time.sleep):
    """Return DIRECTORY/NAME, downloading or resuming it as needed."""
    spec = MEMBERS[name]
    directory = Path(directory)
    final = directory / name
    if final.is_file() and not final.is_symlink() and final.stat().st_size == spec["size"]:
        return final
    if spec["method"] == 0 and name in chunk_pins()[1]:
        return fetch_chunked(name, directory, progress, cancel, opener, sleep)
    part = directory / (name + ".part")
    state_path = directory / (name + ".part.json")
    opener = opener or urllib.request.build_opener(AppleRedirect()).open
    state = {}
    if part.exists() and state_path.exists():
        try:
            state = json.loads(state_path.read_text())
        except ValueError:
            state = {}
    done = part.stat().st_size if part.exists() else 0
    if state.get("member") != spec["member"] or state.get("done", -1) > done:
        done, state = 0, {}
    need = spec["compressed"] - done + (spec["size"] if spec["method"] == 8 else 0)
    if shutil.disk_usage(directory).free < need + (64 << 20):
        raise ValueError(f"Not enough free space to download {name}: need {need >> 20} MiB")
    start = state.get("start") or _data_start(opener, spec)
    crc = state.get("crc", 0) if done else 0
    done = state.get("done", 0) if done else 0
    fd = os.open(part, os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    failures = 0
    with os.fdopen(fd, "wb") as out:
        out.truncate(done)
        out.seek(done)
        last_sync = time.monotonic()
        while done < spec["compressed"]:
            if cancel and cancel():
                raise Cancelled()
            try:
                response = _range(opener, spec["ipsw"], start + done, start + spec["compressed"] - 1,
                                  IPSW_SIZE[spec["ipsw"]])
                with response:
                    while done < spec["compressed"]:
                        if cancel and cancel():
                            raise Cancelled()
                        chunk = response.read(min(CHUNK, spec["compressed"] - done))
                        if not chunk:
                            raise ConnectionError("connection closed")
                        out.write(chunk)
                        if spec["method"] == 0:
                            crc = zlib.crc32(chunk, crc)
                        done += len(chunk)
                        failures = 0
                        if progress:
                            progress(done, spec["compressed"])
                        if time.monotonic() - last_sync > 5:
                            out.flush()
                            os.fsync(out.fileno())
                            _private(state_path, json.dumps(
                                {"member": spec["member"], "start": start, "done": done, "crc": crc}))
                            last_sync = time.monotonic()
            except Cancelled:
                out.flush()
                os.fsync(out.fileno())
                _private(state_path, json.dumps(
                    {"member": spec["member"], "start": start, "done": done, "crc": crc}))
                raise
            except (OSError, urllib.error.URLError, ConnectionError) as exc:
                failures += 1
                if failures > RETRIES:
                    out.flush()
                    _private(state_path, json.dumps(
                        {"member": spec["member"], "start": start, "done": done, "crc": crc}))
                    raise ValueError(f"Download of {name} keeps failing ({exc}); try again later") from exc
                sleep(min(30, 2 ** failures))
        out.flush()
        os.fsync(out.fileno())
    if spec["method"] == 8:
        inflated = directory / (name + ".inflate")
        crc, size, sha = 0, 0, hashlib.sha256()
        engine = zlib.decompressobj(-15)
        with open(part, "rb") as source, os.fdopen(os.open(
                inflated, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600), "wb") as out:
            while block := source.read(CHUNK):
                data = engine.decompress(block)
                crc, size = zlib.crc32(data, crc), size + len(data)
                sha.update(data)
                out.write(data)
            data = engine.flush()
            crc, size = zlib.crc32(data, crc), size + len(data)
            sha.update(data)
            out.write(data)
        part.unlink()
        part = inflated
        actual = sha.hexdigest()
    else:
        size = os.path.getsize(part)
        actual = None
    if size != spec["size"] or crc != spec["crc32"]:
        part.unlink()
        state_path.unlink(missing_ok=True)
        raise ValueError(f"{name} from Apple failed its ZIP checksum; it was removed, retry")
    # A stored archive is authenticated segment by segment when it is
    # decrypted, against the pinned digest of its plain content.
    if actual is not None and actual != spec["sha256"]:
        part.unlink()
        state_path.unlink(missing_ok=True)
        raise ValueError(f"{name} from Apple does not match the pinned macOS 27.2 input")
    os.replace(part, final)
    state_path.unlink(missing_ok=True)
    return final


def clean_partials(directory):
    for name in MEMBERS:
        for suffix in (".part", ".part.json", ".inflate"):
            (Path(directory) / (name + suffix)).unlink(missing_ok=True)
