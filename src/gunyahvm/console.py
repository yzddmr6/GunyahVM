# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Bounded replay adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7;
# reconnect/replay idea also from librevm 56918558e2c4303cda803a655ea716869c551b71.
"""Bounded serial replay; a detached frontend never owns the VM process."""

LIMIT = 256 * 1024


def replay(path, limit=LIMIT):
    try:
        with path.open("rb") as stream:
            stream.seek(0, 2)
            stream.seek(max(0, stream.tell() - min(limit, LIMIT)))
            return stream.read(LIMIT).decode("utf-8", errors="replace")
    except FileNotFoundError:
        return ""
