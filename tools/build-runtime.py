#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Materialize the locked runtime into the runtime.json staging contract, offline."""

import argparse
from pathlib import Path
from runtime_inputs import prepare, REPO

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-cache", type=Path, default=REPO / "inputs/prebuilt")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.input_cache, args.output))
