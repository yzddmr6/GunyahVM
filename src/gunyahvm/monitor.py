# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""Collect VM states outside the GTK main loop."""

import threading

TRANSITIONAL = ("starting", "stopping", "restarting")


class StateMonitor:
    """Single collection worker; cached results survive transient collection failures."""

    def __init__(self, on_update, interval=3.0, busy_interval=1.0, collect=None):
        self.on_update, self.interval, self.busy_interval, self.collect = (
            on_update,
            interval,
            busy_interval,
            collect,
        )
        self.vmids = []
        self.last = None
        self.forced = True
        self.wake = threading.Event()
        self.stopped = False
        self.lock = threading.Lock()
        self.t = None

    def set_vms(self, vmids):
        with self.lock:
            self.vmids = list(vmids)
        self.kick()

    def kick(self):
        """Poll now and report even when nothing changed (after an action, or a new VM list)."""
        self.forced = True
        self.wake.set()

    def start(self):
        if self.t is None:
            self.t = threading.Thread(target=self.loop, name="vm-states", daemon=True)
            self.t.start()

    def stop(self):
        self.stopped = True
        self.wake.set()

    def poll_once(self):
        with self.lock:
            ids = list(self.vmids)
        forced, self.forced = self.forced, False
        res = self.collect(ids)
        if res is None:  # collection unavailable: keep the last answer
            return self.last
        if forced or res != self.last:
            self.last = res
            self.on_update(*res)
        return res

    def loop(self):
        while not self.stopped:
            self.wake.clear()
            try:
                res = self.poll_once()
            except Exception:  # noqa: BLE001 - never kill the poller
                res = self.last
            busy = res is not None and any(
                s["status"]["state"] in TRANSITIONAL for s in res[0].values()
            )
            self.wake.wait(self.busy_interval if busy else self.interval)
