# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Keep the host awake while a VM runs; shut the VM down if the host sleeps anyway.

A running VM inhibits the desktop session's suspend, so idle suspend does not
happen and GNOME locks the screen when the lid closes (gnome-settings-daemon
locks instead of suspending while suspend is inhibited, and the lock blanks
the display). logind would still suspend on the lid, so the guard also takes
its lid-switch lock, but only while the session inhibitor exists: without a
desktop to lock the screen (or outside a local session, where logind refuses
the lid lock), closing the lid keeps suspending.

Sleep that still happens (an explicit suspend) gets a guest shutdown request
through a delay inhibitor. logind's own delay limit applies; the guard does not
raise it, because that limit also bounds every other delay lock on the host.
stop() gets the seconds left for the guest, so that it is stopped, cleanly or
not, before logind stops waiting: a guest is never frozen in place.
"""

import os
import sys
import threading

SESSION_INHIBIT_SUSPEND = 4  # org.gnome.SessionManager.Inhibit flags
APP_ID = "io.github.yzddmr6.GunyahVM"
# Forced termination waits 5 s before SIGKILL, then networking is cleaned up.
TEARDOWN_MARGIN = 8


class SleepGuard:
    def __init__(self, stop):
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib

        self.loop = GLib.MainLoop()
        mainloop = DBusGMainLoop()
        self.bus = dbus.SystemBus(private=True, mainloop=mainloop)
        obj = self.bus.get_object("org.freedesktop.login1", "/org/freedesktop/login1")
        props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
        manager = dbus.Interface(obj, "org.freedesktop.login1.Manager")
        delay = int(props.Get("org.freedesktop.login1.Manager", "InhibitDelayMaxUSec"))
        limit = max(0.0, delay / 1_000_000 - TEARDOWN_MARGIN)
        self.lock = threading.Lock()
        self.lid_fd = None
        self.fds = [
            manager.Inhibit(
                "sleep", "GunyahVM", "Shut down virtual machines before sleep", "delay"
            ).take()
        ]
        self.session = None
        try:
            self.session = dbus.SessionBus(private=True, mainloop=mainloop)
            sm = dbus.Interface(
                self.session.get_object("org.gnome.SessionManager", "/org/gnome/SessionManager"),
                "org.gnome.SessionManager",
            )
            sm.Inhibit(APP_ID, dbus.UInt32(0), "A virtual machine is running",
                       dbus.UInt32(SESSION_INHIBIT_SUSPEND))
            self.lid_fd = manager.Inhibit(
                "handle-lid-switch", "GunyahVM",
                "A virtual machine is running; the desktop locks the screen instead",
                "block",
            ).take()
            # The session inhibitor ends with gnome-session; the lid lock must
            # not outlive it, or closing the lid would neither lock nor sleep.
            self.session.watch_name_owner("org.gnome.SessionManager", self._session_owner)
        except dbus.DBusException as exc:
            # No desktop to lock the screen, or not allowed to hold the lid:
            # closing the lid shuts the VM down and sleeps, as before.
            print(f"Host sleep stays enabled while this VM runs: {exc}",
                  file=sys.stderr, flush=True)
            self._release_lid()
            if self.session is not None:
                self.session.close()
                self.session = None
        self.match = self.bus.add_signal_receiver(
            lambda preparing: stop(limit) if preparing else None,
            signal_name="PrepareForSleep",
            dbus_interface="org.freedesktop.login1.Manager",
            path="/org/freedesktop/login1",
        )
        if props.Get("org.freedesktop.login1.Manager", "PreparingForSleep"):
            stop(limit)
        self.thread = threading.Thread(target=self.loop.run, daemon=True)
        self.thread.start()

    def _session_owner(self, owner):
        if not owner:
            self._release_lid()

    def _release_lid(self):
        with self.lock:
            if self.lid_fd is not None:
                os.close(self.lid_fd)
                self.lid_fd = None

    def close(self):
        self.match.remove()
        self._release_lid()
        for fd in self.fds:
            os.close(fd)
        if self.session is not None:
            # The session inhibitor belongs to this connection.
            self.session.close()
        self.loop.quit()
        self.bus.close()
        self.thread.join(timeout=2)
