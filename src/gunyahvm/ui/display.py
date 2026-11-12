# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Embedded crosvm display using Casilda's upstream Wayland compositor.

The guest's display protocol remains virtio-gpu or simplefb. This widget only
terminates the host Wayland connection; it does not forward guest applications
or give the guest access to the desktop compositor.
"""

import os
from pathlib import Path
import stat
from ..storage import display_socket

import gi

gi.require_version("Casilda", "1.0")
from gi.repository import Casilda, Gdk, Graphene, Gsk, Gtk


class VmDisplay(Gtk.Widget):
    def __init__(self, vm_id, width=1280, height=800):
        super().__init__(vexpand=True, hexpand=True)
        self.guest_width, self.guest_height = width, height
        runtime = Path(f"/run/user/{os.getuid()}")
        if runtime.stat().st_uid != os.getuid():
            raise ValueError("Runtime directory belongs to another user")
        # crosvm accepts a socket path. A private directory supplies the local
        # account boundary without a TCP listener or unauthenticated VNC.
        # A stable parent also remains visible in the VMM's mount namespace
        # after a frontend crash. Never replace that directory while VM runs.
        self.socket_path = display_socket(vm_id)
        self.directory = self.socket_path.parent
        self.directory.mkdir(mode=0o700, exist_ok=True)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError("Display directory is not private to this user")
        self.compositor = Casilda.Compositor(socket=str(self.socket_path))
        self.compositor.set_hexpand(True)
        self.compositor.set_vexpand(True)
        self.compositor.set_parent(self)
        if not self.socket_path.is_socket():
            self.compositor.unparent()
            raise RuntimeError("Embedded Wayland compositor did not create its socket")

    def focus(self):
        self.compositor.grab_focus()

    def display_scale(self):
        native = self.get_native()
        surface = native.get_surface() if native else None
        return surface.get_scale() if surface else self.get_scale_factor()

    def do_measure(self, orientation, for_size):
        natural = self.guest_width if orientation == Gtk.Orientation.HORIZONTAL else self.guest_height
        return 160, round(natural / max(1, self.display_scale())), -1, -1

    def do_size_allocate(self, width, height, baseline):
        # GTK owns both drawing and the inverse transform used for input. Fit
        # the fixed guest scanout without changing its mode or aspect ratio.
        # This also works for firmware/simplefb guests without a resize agent.
        scale = max(1, self.display_scale())
        w, h = self.guest_width / scale, self.guest_height / scale
        zoom = min(width / w, height / h)
        point = Graphene.Point()
        point.init((width - w * zoom) / 2, (height - h * zoom) / 2)
        transform = Gsk.Transform().translate(point).scale(zoom, zoom)
        self.compositor.allocate(round(w), round(h), -1, transform)

    def do_snapshot(self, snapshot):
        # Explicitly paint letterboxing; the desktop's light theme must not
        # shine through an opaque guest display.
        color = Gdk.RGBA()
        color.parse("#17191c")
        bounds = Graphene.Rect()
        bounds.init(0, 0, self.get_width(), self.get_height())
        snapshot.append_color(color, bounds)
        self.snapshot_child(self.compositor, snapshot)

    def dispose_display(self):
        if self.compositor.get_parent() is not None:
            self.compositor.unparent()
