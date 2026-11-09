#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Download hash-locked GunyahVM inputs and the upstream signed driver archive."""

import argparse
import json
from pathlib import Path
from prebuilt_inputs import REPO, fetch


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=REPO / "inputs/prebuilt")
    parser.add_argument("--offline", action="store_true")
    args = parser.parse_args()
    own = json.loads((REPO / "tools/prebuilt.lock.json").read_text())["release"]
    drivers = json.loads((REPO / "tools/windows.lock.json").read_text())["drivers"]
    runtime = json.loads((REPO / "tools/runtime.lock.json").read_text())["input"]
    openssh = json.loads((REPO / "tools/windows.lock.json").read_text())["openssh"]
    for item in (own, drivers, runtime, openssh):
        print(fetch(item, args.cache, args.offline))


if __name__ == "__main__":
    main()
