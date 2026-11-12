# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Opt-in text clipboard over the existing authenticated guest channel."""

import os
from pathlib import Path
import selectors
import shlex
import subprocess
import time

from . import core, windows_agent

MAX_TEXT = 1024 * 1024


def exchange(argv, data=b"", timeout=15):
    """Bound both output and time, including a hostile or broken guest agent."""
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.DEVNULL)
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        with selectors.DefaultSelector() as poll:
            os.set_blocking(process.stdout.fileno(), False)
            poll.register(process.stdout, selectors.EVENT_READ)
            if data:
                os.set_blocking(process.stdin.fileno(), False)
                poll.register(process.stdin, selectors.EVENT_WRITE)
            else:
                process.stdin.close()
            while poll.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise ValueError("Guest clipboard timed out")
                for key, _ in poll.select(remaining):
                    if key.fileobj is process.stdin:
                        try:
                            count = os.write(key.fd, data[:65536])
                            data = data[count:]
                        except BrokenPipeError:
                            data = b""
                        if not data:
                            poll.unregister(process.stdin)
                            process.stdin.close()
                    else:
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            poll.unregister(process.stdout)
                        output.extend(chunk)
                        if len(output) > MAX_TEXT:
                            raise ValueError("Clipboard text exceeds 1 MiB")
        if process.wait(timeout=max(.01, deadline - time.monotonic())):
            raise ValueError("Guest clipboard unavailable; check the desktop session and guest tools")
        result = bytes(output).decode("utf-8")
        if "\0" in result:
            raise ValueError("Clipboard requires text without NUL")
        return result
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        process.stdin.close()
        process.stdout.close()


def transfer(vm, cfg, action, text=""):
    if action not in ("probe", "read", "write"):
        raise ValueError("Invalid clipboard action")
    data = text.encode("utf-8")
    if len(data) > MAX_TEXT or "\0" in text:
        raise ValueError("Clipboard requires UTF-8 text of at most 1 MiB without NUL")
    if cfg["os"] == "windows11":
        raise ValueError("Windows text clipboard needs a desktop-session guest agent")
    state = core.status(vm, cfg)
    if not state.get("guest_ready") or not state.get("ipv4") or not core.vmm_active(state):
        raise ValueError("Wait for the guest's authenticated control channel")
    code = Path(__file__).with_name("guest-clipboard.py").read_text()
    command = "python3 -c " + shlex.quote(code) + " " + action
    identity, user = Path(cfg["ssh_identity"]), cfg["username"]
    argv = windows_agent.win_ssh_argv([identity], user, state["ipv4"], vm / "known_hosts", command)
    return exchange(argv, data if action == "write" else b"")
