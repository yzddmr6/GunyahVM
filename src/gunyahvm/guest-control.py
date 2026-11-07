#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Minimal hvc0 poweroff channel, adapted from iceland-gvm's guest-agent approach.
import json
import os
import subprocess
import tty
from pathlib import Path

subprocess.run(["cloud-init", "status", "--wait"], check=True)
fd = os.open("/dev/hvc0", os.O_RDWR | os.O_NOCTTY)
tty.setraw(fd)
addresses = json.loads(
    subprocess.check_output(["ip", "-j", "-4", "addr", "show"], text=True)
)
ips = [
    a["local"]
    for i in addresses
    for a in i.get("addr_info", [])
    if a.get("scope") == "global"
]
ready = {
    "ipv4": ips,
    "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
}
os.write(fd, ("\nGUNYAHVM_READY " + json.dumps(ready) + "\n").encode())
buf = b""
while True:
    data = os.read(fd, 1024)
    if not data:
        break
    buf += data
    while b"\n" in buf:
        line, buf = buf.split(b"\n", 1)
        if line.strip() == b"poweroff":
            os.write(fd, b"GUNYAHVM_POWEROFF_REQUEST\n")
            subprocess.run(["systemctl", "poweroff", "--no-block"], check=True)
        else:
            os.write(fd, b"GUNYAHVM_UNKNOWN_COMMAND\n")
    if len(buf) > 4096:
        buf = b""
