#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Install the GunyahVM guest files from the current NoCloud seed.

cloud-init writes these files once per instance, so a VM created by an older
GunyahVM would otherwise keep that version's guest scripts. cloud-init reads
the seed again on every boot and runs bootcmd each time, before sysinit.target
and therefore before the guest setup and the display manager start. The seed
lists only files that GunyahVM owns; accounts, keys, packages and the user's
own configuration are left alone.
"""

import json
import os
import subprocess
import sys
from pathlib import Path


def refresh(seed, root=Path("/")):
    """Install every listed file whose content or mode differs; return the changed paths."""
    changed = []
    for item in json.loads((seed / "guest-files.json").read_text()):
        path = Path(item["path"])
        if not path.is_absolute() or ".." in path.parts or "/" in item["source"]:
            raise ValueError("invalid guest file entry: " + item["path"])
        data = (seed / item["source"]).read_bytes()
        mode = int(item["permissions"], 8)
        target = root / path.relative_to("/")
        try:
            if target.read_bytes() == data and target.stat().st_mode & 0o7777 == mode:
                continue
        except FileNotFoundError:
            pass
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name("." + target.name + ".gunyahvm")
        part.write_bytes(data)
        part.chmod(mode)
        os.replace(part, target)
        changed.append(str(path))
    return changed


def main(argv):
    changed = refresh(Path(argv[0]))
    if any(p.startswith("/etc/systemd/") for p in changed):
        subprocess.run(["systemctl", "daemon-reload"], check=False)
    for path in changed:
        print("gunyahvm: updated " + path, flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
