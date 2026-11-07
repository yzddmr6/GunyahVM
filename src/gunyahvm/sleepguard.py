# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Ordinary-user logind delay inhibitor, held until the VM and TAP are gone."""

import os
import threading


class SleepGuard:
    def __init__(self, stop):
        import dbus
        from dbus.mainloop.glib import DBusGMainLoop
        from gi.repository import GLib

        self.loop = GLib.MainLoop()
        self.bus = dbus.SystemBus(private=True, mainloop=DBusGMainLoop())
        obj = self.bus.get_object("org.freedesktop.login1", "/org/freedesktop/login1")
        props = dbus.Interface(obj, "org.freedesktop.DBus.Properties")
        delay = int(props.Get("org.freedesktop.login1.Manager", "InhibitDelayMaxUSec"))
        if delay < 300_000_000:
            self.bus.close()
            raise ValueError(
                "logind sleep delay must be at least 300 seconds; install the GunyahVM logind drop-in and restart logind"
            )
        manager = dbus.Interface(obj, "org.freedesktop.login1.Manager")
        self.fd = manager.Inhibit(
            "sleep", "GunyahVM", "Shut down virtual machines before sleep", "delay"
        ).take()
        self.match = self.bus.add_signal_receiver(
            lambda preparing: stop() if preparing else None,
            signal_name="PrepareForSleep",
            dbus_interface="org.freedesktop.login1.Manager",
            path="/org/freedesktop/login1",
        )
        if props.Get("org.freedesktop.login1.Manager", "PreparingForSleep"):
            stop()
        self.thread = threading.Thread(target=self.loop.run, daemon=True)
        self.thread.start()

    def close(self):
        self.match.remove()
        os.close(self.fd)
        self.loop.quit()
        self.bus.close()
        self.thread.join(timeout=2)
