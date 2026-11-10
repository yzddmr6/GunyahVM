# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""Per-VM settings (saved as soon as they change) and application preferences."""

from copy import deepcopy
from pathlib import Path
import re

from gi.repository import Adw, GLib, Gtk

from .. import config, core, updates
from . import text, util
from .i18n import _

ACCELERATED = ("2d", "virgl", "venus", "native")
MAX_FORWARDS, MAX_SHARES = 16, 4


def share_tag(path, taken):
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", Path(path).name).strip("-")[:28] or "share"
    tag, n = base, 2
    while tag in taken or tag == "gvmtools":
        tag, n = f"{base}-{n}", n + 1
    return tag


def mount_command(tag):
    return f"sudo mkdir -p /mnt/{tag} && sudo mount -t 9p -o trans=virtio {tag} /mnt/{tag}"


class VmSettings(Adw.PreferencesDialog):
    def __init__(self, window, value):
        super().__init__(search_enabled=False, content_width=640, content_height=760)
        self.window = window
        self.cfg = deepcopy(value["config"])
        self.name = self.cfg["name"]
        self.set_title(_("{name} Settings").format(name=self.name))
        self.windows = self.cfg["os"] == "windows11"
        self.snapshot = window.snapshot
        self.features = self.snapshot.features()
        self.components = self.snapshot.runtime.get("components", {})
        self.loading = True
        self.pages, self.groups = [], []
        self.forward_rows, self.share_rows = [], []
        self.build_general()
        self.build_display()
        self.build_network()
        if not self.windows:
            self.build_sharing()
        self.build_advanced()
        self.load()
        self.loading = False

    # Structure ------------------------------------------------------------

    def page(self, title, icon):
        page = Adw.PreferencesPage(title=title, icon_name=icon)
        page.set_banner(Adw.Banner(title=_("Shut down the virtual machine to change its settings")))
        self.add(page)
        self.pages.append(page)
        return page

    def group(self, page, title=None, description=None):
        group = Adw.PreferencesGroup()
        if title:
            group.set_title(title)
        if description:
            group.set_description(description)
        page.add(group)
        self.groups.append(group)
        return group

    def set_running(self, running):
        for page in self.pages:
            page.get_banner().set_revealed(running)
        for group in self.groups:
            group.set_sensitive(not running)

    def build_general(self):
        page = self.page(_("General"), "computer-symbolic")
        group = self.group(page, _("Resources"))
        cpus, memory = util.resource_limits(self.snapshot)
        self.cpu = util.spin(_("Processors"), 1, max(cpus, self.cfg["cpus"]), 1, self.cfg["cpus"])
        self.cpu.connect("notify::value", lambda *_: self.apply(
            lambda c: c.update(cpus=int(self.cpu.get_value()))))
        minimum = 4608 if self.windows else 512
        values = text.memory_choices(minimum, max(memory, minimum), self.cfg["memory_mib"])
        self.memory = util.combo(_("Memory"), util.memory_items(values))
        self.memory.connect("notify::selected", lambda *_: self.apply(
            lambda c: c.update(memory_mib=util.combo_key(self.memory))))
        disk = Adw.ActionRow(
            title=_("Disk Size"), subtitle=_("Set when the virtual machine was created")
        )
        size = Gtk.Label(label=text.disk(self.cfg["disk_gib"]))
        size.add_css_class("dim-label")
        disk.add_suffix(size)
        for row in (self.cpu, self.memory, disk):
            group.add(row)

    def build_display(self):
        page = self.page(_("Display"), "video-display-symbolic")
        group = self.group(page)
        self.gpu = util.combo(_("Graphics"), [])
        self.gpu.connect("notify::selected", lambda *_: self.apply(self.set_gpu))
        self.resolution = util.combo(_("Resolution"), [])
        self.resolution.connect("notify::selected", lambda *_: self.apply(self.set_resolution))
        group.add(self.gpu)
        group.add(self.resolution)
        self.fullscreen = self.input = self.vnc = None
        if self.windows:
            return
        self.fullscreen = Adw.SwitchRow(
            title=_("Full Screen"), subtitle=_("Open the display window in full screen.")
        )
        self.fullscreen.connect("notify::active", lambda *_: self.apply(
            lambda c: c["display"].update(fullscreen=self.fullscreen.get_active())))
        self.input = util.combo(_("Touch Input"), [(k, _(v[0])) for k, v in text.INPUT.items()])
        self.input.connect("notify::selected", lambda *_: self.apply(
            lambda c: c["display"].update(input_mode=util.combo_key(self.input))))
        group.add(self.fullscreen)
        group.add(self.input)
        remote = self.group(page, _("Remote Display"))
        self.vnc = Adw.SwitchRow(title=_("Allow VNC Connections"))
        self.vnc.connect("notify::active", lambda *_: self.apply(
            lambda c: c["display"].update(vnc=self.vnc.get_active())))
        remote.add(self.vnc)

    def build_network(self):
        page = self.page(_("Network"), "network-wired-symbolic")
        group = self.group(page)
        self.network = util.combo(_("Network"), [(k, _(v)) for k, v in text.NETWORK.items()])
        self.network.connect("notify::selected", lambda *_: self.apply(
            lambda c: c.update(network_mode=util.combo_key(self.network))))
        group.add(self.network)
        self.forwards = self.group(
            page, _("Port Forwarding"),
            _("Reach a service in the virtual machine at localhost on this device. Other accounts on this device can reach it too."),
        )
        self.add_forward = Adw.ButtonRow(
            title=_("_Add Port Forward…"), use_underline=True, start_icon_name="list-add-symbolic"
        )
        self.add_forward.connect("activated", lambda *_: self.forward_dialog())
        self.forwards.add(self.add_forward)

    def build_sharing(self):
        page = self.page(_("Sharing"), "folder-symbolic")
        description = _("The virtual machine can read and change files in these folders. In Linux, mount a folder with the command from its copy button.")
        if not self.features["shares"]:
            description = _("Shared folders aren't supported by the installed runtime.")
        self.shares = self.group(page, _("Shared Folders"), description)
        self.add_share = Adw.ButtonRow(
            title=_("_Add Folder…"), use_underline=True, start_icon_name="list-add-symbolic"
        )
        self.add_share.connect("activated", lambda *_: self.choose_folder())
        self.shares.add(self.add_share)

    def build_advanced(self):
        page = self.page(_("Advanced"), "applications-system-symbolic")
        self.protection = self.payload = self.subnet = self.vhost = None
        if not self.windows:
            group = self.group(page, _("System"))
            if self.features["shared"] or self.cfg["memory_mode"] == "shared":
                self.protection = Adw.SwitchRow(
                    title=_("Memory Protection"),
                    subtitle=_("Keeps the host from reading the virtual machine's memory. The native GPU mode needs it on."),
                )
                self.protection.connect("notify::active", lambda *_: self.apply(self.set_protection))
                group.add(self.protection)
            self.payload = Adw.SwitchRow(title=_("GunyahVM Guest Kernel"))
            self.payload.connect("notify::active", lambda *_: self.apply(
                lambda c: c.update(guest_payload=self.payload.get_active())))
            group.add(self.payload)
            group = self.group(page, _("Network"))
            self.subnet = Adw.EntryRow(title=_("Private Subnet"), show_apply_button=True)
            self.subnet.connect("apply", lambda *_: self.apply(
                lambda c: c.update(subnet=self.subnet.get_text().strip())))
            self.vhost = Adw.SwitchRow(title=_("vhost-net Acceleration"))
            self.vhost.connect("notify::active", lambda *_: self.apply(
                lambda c: c.update(vhost_net=self.vhost.get_active())))
            group.add(self.subnet)
            group.add(self.vhost)
        group = self.group(page, _("Remote Display"))
        display = self.cfg["display"]
        self.vnc_port = util.spin(_("VNC Port"), 1024, 65535, 1, display["vnc_port"])
        self.vnc_port.connect("notify::value", lambda *_: self.apply(
            lambda c: c["display"].update(vnc_port=int(self.vnc_port.get_value()))))
        group.add(self.vnc_port)

    # Changes --------------------------------------------------------------

    def set_gpu(self, cfg):
        mode = util.combo_key(self.gpu)
        if mode:
            cfg["display"]["gpu"] = mode

    def set_resolution(self, cfg):
        key = util.combo_key(self.resolution)
        if key:
            width, height = (int(v) for v in key.split("x"))
            cfg["display"].update(width=width, height=height)

    def set_protection(self, cfg):
        cfg["memory_mode"] = "protected" if self.protection.get_active() else "shared"
        modes = self.snapshot.modes(cfg["memory_mode"], cfg["os"])
        if cfg["display"]["gpu"] not in modes and modes:
            cfg["display"]["gpu"] = "virgl" if "virgl" in modes else modes[-1]

    @staticmethod
    def derive(cfg):
        """Settings that follow from others: accelerated graphics need the GunyahVM guest kernel."""
        display = cfg["display"]
        if display["gpu"] in ACCELERATED or cfg["memory_mode"] == "shared":
            cfg["guest_payload"] = True
        if cfg["os"] == "linux" and display["gpu"] == "none":
            display["vnc"] = False

    def apply(self, change):
        if self.loading:
            return
        cfg = deepcopy(self.cfg)
        change(cfg)
        self.derive(cfg)
        if cfg == self.cfg:
            return
        try:
            cfg = config.validate(cfg)
            core.save_config(self.window.store, self.name, cfg)
        except (OSError, ValueError, RuntimeError) as exc:
            message = util.clean(str(exc))
            self.add_toast(Adw.Toast(title=text.explain(message) or message, timeout=6))
        else:
            self.cfg = cfg
            self.window.reload()
        self.load()

    # Load -----------------------------------------------------------------

    def load(self):
        loading, self.loading = self.loading, True
        try:
            self.load_values()
        finally:
            self.loading = loading

    def load_values(self):
        cfg, display = self.cfg, self.cfg["display"]
        self.cpu.set_value(cfg["cpus"])
        util.combo_select(self.memory, cfg["memory_mib"])

        modes = self.snapshot.modes(cfg["memory_mode"], cfg["os"])
        if display["gpu"] not in modes:
            modes = modes + [display["gpu"]]
        util.combo_set_items(self.gpu, [(m, text.graphics(m)) for m in modes])
        util.combo_select(self.gpu, display["gpu"])
        if self.windows:
            self.gpu.set_sensitive(False)
            self.gpu.set_subtitle(_("Windows uses the basic display."))
        elif display["gpu"] not in self.snapshot.modes(cfg["memory_mode"], cfg["os"]):
            self.gpu.set_subtitle(_("Not available on this device right now."))
        else:
            self.gpu.set_subtitle(text.graphics_help(display["gpu"]))

        sizes = list(text.RESOLUTIONS)
        current = (display["width"], display["height"])
        if current not in sizes:
            sizes = sorted(sizes + [current])
        util.combo_set_items(
            self.resolution, [(f"{w}x{h}", text.resolution(w, h)) for w, h in sizes]
        )
        util.combo_select(self.resolution, "{}x{}".format(*current))
        self.resolution.set_sensitive(display["gpu"] != "none")

        if self.fullscreen:
            self.fullscreen.set_active(display["fullscreen"])
            self.fullscreen.set_sensitive(display["gpu"] != "none" and not display["vnc"])
            util.combo_select(self.input, display["input_mode"])
            mode = display["input_mode"]
            if self.features["input_modes"]:
                self.input.set_sensitive(display["gpu"] != "none" and not display["vnc"])
                self.input.set_subtitle(_(text.INPUT[mode][1]))
            else:
                self.input.set_sensitive(False)
                self.input.set_subtitle(_("Not supported by the installed runtime."))
            self.vnc.set_active(display["vnc"])
            self.vnc.set_sensitive(self.features["vnc"] and display["gpu"] != "none")
            if not self.features["vnc"]:
                self.vnc.set_subtitle(_("Not supported by the installed runtime."))
            elif display["gpu"] == "none":
                self.vnc.set_subtitle(_("Needs a graphics mode."))
            else:
                self.vnc.set_subtitle(_("Shows the display in a VNC viewer instead of a window. Any account on this device can connect without a password."))

        util.combo_select(self.network, cfg["network_mode"])
        self.load_forwards()
        if not self.windows:
            self.load_shares()

        if self.protection:
            self.protection.set_active(cfg["memory_mode"] == "protected")
        if self.payload:
            required = display["gpu"] in ACCELERATED or cfg["memory_mode"] == "shared"
            installed = "guest_kernel" in self.components
            self.payload.set_active(cfg["guest_payload"])
            self.payload.set_sensitive(installed and not required)
            if required:
                self.payload.set_subtitle(_("Required by the selected graphics mode."))
            elif not installed:
                self.payload.set_subtitle(_("Not installed."))
            else:
                self.payload.set_subtitle(_("When off, the kernel inside the disk image is used."))
        if self.subnet:
            self.subnet.set_text(cfg["subnet"])
            self.vhost.set_active(cfg["vhost_net"])
            self.vhost.set_sensitive(self.features["vhost_net"] or cfg["vhost_net"])
            self.vhost.set_subtitle(
                _("Faster networking through the host kernel.") if self.features["vhost_net"]
                else _("Not supported on this device.")
            )
        self.vnc_port.set_value(display["vnc_port"])
        self.vnc_port.set_sensitive(display["vnc"])

    def load_forwards(self):
        for row in self.forward_rows:
            self.forwards.remove(row)
        self.forwards.remove(self.add_forward)
        self.forward_rows = []
        for index, forward in enumerate(self.cfg["forwards"]):
            row = Adw.ActionRow(
                title="{} {} → {}".format(forward["proto"].upper(), forward["host"], forward["guest"]),
                subtitle=_("localhost:{host} on this device to port {guest} in the virtual machine").format(
                    host=forward["host"], guest=forward["guest"]),
            )
            row.add_suffix(util.icon_button(
                "user-trash-symbolic", _("Remove"),
                lambda i=index: self.apply(lambda c: c["forwards"].pop(i)),
            ))
            self.forwards.add(row)
            self.forward_rows.append(row)
        self.forwards.add(self.add_forward)
        self.add_forward.set_sensitive(len(self.cfg["forwards"]) < MAX_FORWARDS)

    def load_shares(self):
        for row in self.share_rows:
            self.shares.remove(row)
        self.shares.remove(self.add_share)
        self.share_rows = []
        for index, share in enumerate(self.cfg["shares"]):
            row = Adw.ActionRow(title=share["tag"], subtitle=share["path"])
            row.add_prefix(Gtk.Image(icon_name="folder-symbolic"))
            row.add_suffix(util.icon_button(
                "edit-copy-symbolic", _("Copy Mount Command"),
                lambda tag=share["tag"]: self.copy_mount(tag),
            ))
            row.add_suffix(util.icon_button(
                "user-trash-symbolic", _("Remove"),
                lambda i=index: self.apply(lambda c: c["shares"].pop(i)),
            ))
            self.shares.add(row)
            self.share_rows.append(row)
        self.shares.add(self.add_share)
        self.add_share.set_sensitive(
            self.features["shares"] and len(self.cfg["shares"]) < MAX_SHARES
        )

    # Lists ----------------------------------------------------------------

    def copy_mount(self, tag):
        util.copy(self, mount_command(tag))
        self.add_toast(Adw.Toast(title=_("Mount command copied")))

    def choose_folder(self):
        dialog = Gtk.FileDialog(title=_("Choose a Folder to Share"), modal=True)
        dialog.select_folder(self.window, None, self.folder_chosen)

    def folder_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        if folder is None or folder.get_path() is None:
            return
        path = folder.get_path()
        taken = {s["tag"] for s in self.cfg["shares"]}
        self.apply(lambda c: c["shares"].append(
            {"path": path, "tag": share_tag(path, taken), "ro": False}))

    def forward_dialog(self):
        used = {(f["proto"], f["host"]) for f in self.cfg["forwards"]}
        port = 2222
        while ("tcp", port) in used:
            port += 1
        protocol = Adw.ToggleGroup(valign=Gtk.Align.CENTER)
        protocol.add(Adw.Toggle(name="tcp", label="TCP"))
        protocol.add(Adw.Toggle(name="udp", label="UDP"))
        protocol.set_active_name("tcp")
        proto_row = Adw.ActionRow(title=_("Protocol"))
        proto_row.add_suffix(protocol)
        host = util.spin(_("Port on This Device"), 1024, 65535, 1, port)
        guest = util.spin(_("Port in the Virtual Machine"), 1, 65535, 1, 22)
        rows = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        rows.add_css_class("boxed-list")
        for row in (proto_row, host, guest):
            rows.append(row)
        dialog = Adw.AlertDialog(heading=_("Add Port Forward"), extra_child=rows)
        dialog.add_response("cancel", _("_Cancel"))
        dialog.add_response("add", _("_Add"))
        dialog.set_response_appearance("add", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("add")
        dialog.set_close_response("cancel")

        def check(*_):
            key = (protocol.get_active_name(), int(host.get_value()))
            duplicate = key in used
            dialog.set_body(
                _("Port {port} is already forwarded.").format(port=key[1]) if duplicate else ""
            )
            dialog.set_response_enabled("add", not duplicate)

        protocol.connect("notify::active-name", check)
        host.connect("notify::value", check)
        check()

        def response(_dialog, answer):
            if answer != "add":
                return
            forward = {
                "proto": protocol.get_active_name(),
                "host": int(host.get_value()),
                "guest": int(guest.get_value()),
            }
            self.apply(lambda c: c["forwards"].append(forward))

        dialog.connect("response", response)
        dialog.present(self)


class Preferences(Adw.PreferencesDialog):
    def __init__(self, window):
        super().__init__(title=_("Preferences"), search_enabled=False, content_height=320)
        self.window = window
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title=_("Updates"))
        self.check_updates = Adw.SwitchRow(
            title=_("Check for Updates"),
            subtitle=_("Ask GitHub once a day whether a new version is out. No device information is sent."),
            active=updates.enabled(),
        )
        if updates.os.environ.get("GUNYAHVM_NO_UPDATE_CHECK") == "1":
            self.check_updates.set_sensitive(False)
            self.check_updates.set_subtitle(
                _("Turned off by the GUNYAHVM_NO_UPDATE_CHECK environment variable.")
            )
        self.check_updates.connect("notify::active", self.changed)
        group.add(self.check_updates)
        page.add(group)
        self.add(page)

    def changed(self, row, _property):
        try:
            updates.set_enabled(row.get_active())
        except OSError as exc:
            util.error(self, _("Couldn't Save Preferences"), str(exc))
            return
        self.window.hide_update()
        if row.get_active():
            self.window.check_updates()
