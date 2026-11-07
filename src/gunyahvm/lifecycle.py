# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Explicit transitions and clean-shutdown classification shared by frontends."""

ALLOWED = {
    "created": {"starting"},
    "stopped": {"starting"},
    "failed": {"starting"},
    "interrupted": {"starting"},
    "starting": {"running", "failed", "stopping"},
    "running": {"stopping", "stopped", "failed", "restarting"},
    "stopping": {"stopped", "failed"},
    "restarting": {"starting", "failed"},
}


def transition(state, target, **values):
    current = state["state"]
    if target != current and target not in ALLOWED.get(current, set()):
        raise ValueError(f"invalid lifecycle transition: {current} -> {target}")
    return {**state, **values, "state": target}


def outcome(returncode, forced, requested, acknowledged):
    restarting = returncode == 32 and not forced and not requested
    clean = returncode == 0 and not forced and (not requested or acknowledged)
    # A requested Windows stop needs a successful authenticated agent request;
    # VMM process death or a timeout never counts as acknowledgment.
    return {
        "state": "restarting" if restarting else "stopped" if clean else "failed",
        "exit_code": returncode,
        "clean_shutdown": clean,
        "forced_stop": forced,
        "stop_requested": requested,
        "poweroff_acknowledged": acknowledged,
    }
