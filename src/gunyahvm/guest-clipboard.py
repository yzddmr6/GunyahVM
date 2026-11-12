#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""One explicit text exchange in the authenticated Linux desktop account."""

import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import time


def main(action):
    runtime = Path("/run/user/" + str(os.getuid()))
    if runtime.stat().st_uid != os.getuid():
        raise ValueError("No desktop session")
    for program in ("wl-copy", "wl-paste"):
        if not shutil.which(program):
            raise ValueError("Install wl-clipboard in the guest")
    result = subprocess.run(["systemctl", "--user", "show-environment"],
                            capture_output=True, text=True, timeout=5)
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    name = values.get("WAYLAND_DISPLAY", "")
    if not name or Path(name).name != name or not (runtime / name).is_socket():
        raise ValueError("Log into a Wayland desktop as the VM's SSH account")
    os.environ.update(XDG_RUNTIME_DIR=str(runtime), WAYLAND_DISPLAY=name)
    if action == "probe":
        print("ready")
    elif action == "write":
        data = sys.stdin.buffer.read(1048577)
        if len(data) > 1048576:
            raise ValueError("Text exceeds 1 MiB")
        data.decode("utf-8")
        subprocess.run(["wl-copy", "--type", "text/plain;charset=utf-8"],
                       input=data, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=8, check=True)
    elif action == "read":
        # The host bounds the stream too; no clipboard content enters logs.
        process = subprocess.Popen(["wl-paste", "--no-newline", "--type", "text"],
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            data = bytearray()
            deadline = time.monotonic() + 8
            os.set_blocking(process.stdout.fileno(), False)
            with selectors.DefaultSelector() as poll:
                poll.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0 or not poll.select(remaining):
                        raise ValueError("Guest clipboard timed out")
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        break
                    data.extend(chunk)
                    if len(data) > 1048576:
                        raise ValueError("Text exceeds 1 MiB")
            if process.wait(timeout=max(.01, deadline - time.monotonic())):
                raise ValueError("The guest clipboard has no text")
            data.decode("utf-8")
            sys.stdout.buffer.write(data)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
            process.stdout.close()
    else:
        raise ValueError("Invalid clipboard action")


if __name__ == "__main__":
    main(sys.argv[1])
