# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""New virtual machine: choose a system, confirm a few details, create in the background."""

import os
import json
from pathlib import Path
import pwd
import re
import shutil

from gi.repository import Adw, Gio, GLib, Gtk

from .. import catalogue, core, imageprobe
from . import text, util
from .i18n import N_, _

KINDS = {
    "cloud": (
        N_("Ubuntu 26.04 LTS"),
        N_("Downloaded and set up automatically. Includes a desktop when graphics are on."),
        "folder-download-symbolic",
    ),
    "linux": (
        N_("Linux Disk Image"),
        N_("An installed ARM64 system in a raw or qcow2 image on this device."),
        "drive-harddisk-symbolic",
    ),
    "windows11": (
        N_("Windows 11"),
        N_("Installed offline from a Windows 11 ARM64 ISO that you provide."),
        "media-optical-symbolic",
    ),
    "macos": (
        N_("macOS"),
        N_("Downloaded from Apple's servers and set up on this tablet. About 13 GB."),
        "folder-download-symbolic",
    ),
}
NAME = re.compile(r"[a-z][a-z0-9-]{0,31}")


def sanitize(stem):
    name = re.sub(r"[^a-z0-9-]+", "-", stem.lower())
    name = re.sub(r"-+", "-", re.sub(r"^[^a-z]+", "", name)).strip("-")
    return name[:32].rstrip("-") or "linux"


def login_name():
    try:
        name = pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return "user"
    return name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,19}", name) else "user"


class CreateDialog(Adw.Dialog):
    def __init__(self, window):
        super().__init__(title=_("New Virtual Machine"), content_width=600, content_height=780)
        self.window = window
        self.nav = Adw.NavigationView()
        self.nav.add(self.choose_page())
        self.set_child(self.nav)

    def kinds(self):
        windows = self.window.snapshot.features()["windows"]
        return [k for k in KINDS if k != "windows11" or windows]

    def choose_page(self):
        page = Adw.PreferencesPage(
            description=_("Choose what to run."),
            description_centered=True,
        )
        group = Adw.PreferencesGroup()
        self.choices = {}
        for kind in self.kinds():
            title, subtitle, icon = KINDS[kind]
            row = Adw.ActionRow(title=_(title), subtitle=_(subtitle), activatable=True)
            image = Gtk.Image(icon_name=icon, pixel_size=32, margin_start=4, margin_end=4)
            row.add_prefix(image)
            row.add_suffix(Gtk.Image(icon_name="go-next-symbolic"))
            row.connect("activated", lambda _r, k=kind: self.open(k))
            group.add(row)
            self.choices[kind] = row
        page.add(group)
        view = Adw.ToolbarView(content=page)
        view.add_top_bar(Adw.HeaderBar())
        return Adw.NavigationPage(title=_("New Virtual Machine"), child=view, tag="choose")

    def open(self, kind):
        self.details = MacDetails(self) if kind == "macos" else Details(self, kind)
        self.nav.push(self.details)
        # Focusing the name entry would raise the on-screen keyboard on a tablet.
        GLib.idle_add(lambda: self.set_focus(None) and False)


class MacDetails(Adw.NavigationPage):
    """New macOS VM: Apple's files are downloaded and assembled on this tablet."""

    def __init__(self, dialog):
        super().__init__(title=_("macOS"), tag="macos")
        from .. import macos_create
        self.dialog, self.window, self.path = dialog, dialog.window, None
        page = Adw.PreferencesPage(description=_(
            "macOS 27.2 is downloaded from Apple's servers (about 13 GB) and set up here. "
            "It uses 9 GB of memory and 6 processors. You can pause and resume at any time."))
        group = Adw.PreferencesGroup()
        self.name = Adw.EntryRow(title=_("Name"), text=self.unique("macos"))
        self.name.connect("changed", lambda *_: self.validate())
        group.add(self.name)
        page.add(group)
        group = Adw.PreferencesGroup(
            title=_("macOS Account"),
            description=_("You sign in to macOS with this account. It is an administrator."))
        self.user = Adw.EntryRow(title=_("Account Name"), text=macos_create.default_account())
        self.password = Adw.PasswordEntryRow(title=_("Password"))
        self.confirm = Adw.PasswordEntryRow(title=_("Confirm Password"))
        for row in (self.user, self.password, self.confirm):
            row.connect("changed", lambda *_: self.validate())
            group.add(row)
        page.add(group)
        group = Adw.PreferencesGroup(title=_("Storage"))
        self.disk = util.spin(_("Disk Size (GB)"), 40, 512, 8, 64, subtitle=self.free_space())
        group.add(self.disk)
        self.keep = Adw.SwitchRow(
            title=_("Keep the Downloaded Files"),
            subtitle=_("Creates another macOS VM without downloading again. Uses about 13 GB."))
        group.add(self.keep)
        page.add(group)
        group = Adw.PreferencesGroup()
        advanced = Adw.ExpanderRow(title=_("Advanced"),
                                   subtitle=_("Import a boot bundle assembled earlier"))
        self.folder = Adw.ActionRow(title=_("Local Boot Bundle"), subtitle=_("No file chosen"))
        choose = util.button(label=_("_Choose…"))
        choose.connect("clicked", lambda *_: Gtk.FileDialog(title=_("Choose a Boot Bundle")).select_folder(self.window, None, self.chosen))
        self.folder.add_suffix(choose)
        advanced.add_row(self.folder)
        group.add(advanced)
        page.add(group)
        self.create_button = util.button(label=_("C_reate"), css=("suggested-action",))
        self.create_button.connect("clicked", lambda *_: self.create())
        view = Adw.ToolbarView(content=page)
        header = Adw.HeaderBar()
        header.pack_end(self.create_button)
        view.add_top_bar(header)
        self.set_child(view)
        self.validate()

    def unique(self, base):
        name, n = base, 2
        while name in self.window.names() or (self.window.store / "vms" / name).exists():
            name, n = f"{base}-{n}", n + 1
        return name

    def free_space(self):
        store = self.window.store
        try:
            free = shutil.disk_usage(store if store.exists() else Path.home()).free // 1024**3
        except OSError:
            return None
        return _("{size} GB free on this device; about 27 GB are needed while it is created").format(size=free)

    def chosen(self, chooser, result):
        try:
            chosen = chooser.select_folder_finish(result)
            self.path = Path(chosen.get_path())
            data = json.loads((self.path / "inputs.json").read_text())
            if data.get("schema") not in (2, 3):
                raise ValueError(_("This bundle needs to be rebuilt with the native APFS layout."))
            boot = data["boot"]
            self.folder.set_subtitle(_("{memory} MiB · {cpus} processors").format(memory=boot["memory_mib"], cpus=boot["cpus"]))
        except GLib.Error:
            return
        except Exception as error:
            self.path = None
            util.error(self.window, _("Couldn't Open the Bundle"), str(error))
        self.create_button.set_label(_("_Import") if self.path else _("C_reate"))
        self.validate()

    def validate(self):
        from .. import macos_create
        name = self.name.get_text()
        valid = bool(NAME.fullmatch(name)) and name not in self.window.names()
        if self.path is None:
            password, confirm = self.password.get_text(), self.confirm.get_text()
            try:
                macos_create.valid_account(self.user.get_text())
                account = True
                self.user.remove_css_class("error")
            except ValueError:
                account = False
                self.user.add_css_class("error")
            if confirm and confirm != password:
                self.confirm.add_css_class("error")
            else:
                self.confirm.remove_css_class("error")
            valid = valid and account and bool(password) and confirm == password
        self.create_button.set_sensitive(valid)
        return valid

    def create(self):
        if not self.validate():
            return
        name = self.name.get_text()
        self.dialog.close()
        if self.path is not None:
            self.window.create_vm(name, "macos", self.path, None)
            return
        request = {"account": self.user.get_text(), "disk_gib": int(self.disk.get_value()),
                   "keep_downloads": self.keep.get_active()}
        password = self.password.get_text()
        self.password.set_text("")
        self.confirm.set_text("")
        self.window.create_vm(name, "macos-download", request, password)


class Details(Adw.NavigationPage):
    def __init__(self, dialog, kind):
        super().__init__(title=_(KINDS[kind][0]), tag=kind)
        self.dialog, self.window, self.kind = dialog, dialog.window, kind
        self.snapshot = self.window.snapshot
        self.windows = kind == "windows11"
        self.path = None
        self.file_ok = kind == "cloud"
        self.name_edited = False
        self.setting_name = False
        page = Adw.PreferencesPage()
        view = Adw.ToolbarView(content=page)
        header = Adw.HeaderBar()
        self.create_button = util.button(label=_("C_reate"), css=("suggested-action",))
        self.create_button.connect("clicked", lambda *_: self.create())
        header.pack_end(self.create_button)
        view.add_top_bar(header)
        self.set_child(view)

        if kind != "cloud":
            group = Adw.PreferencesGroup()
            self.file = Adw.ActionRow(
                title=_("Windows ISO") if self.windows else _("Disk Image"),
                subtitle=_("No file chosen"),
            )
            choose = util.button(label=_("_Choose…"))
            choose.connect("clicked", lambda *_: self.choose_file())
            self.file.add_suffix(choose)
            self.file.set_activatable_widget(choose)
            group.add(self.file)
            page.add(group)

        self.name_group = Adw.PreferencesGroup()
        self.name = Adw.EntryRow(title=_("Name"))
        self.name.connect("changed", self.name_changed)
        self.name_group.add(self.name)
        page.add(self.name_group)

        if kind == "cloud":
            group = Adw.PreferencesGroup(title=_("Download"))
            self.mirror = util.combo(
                _("Source"), [(k, _(v)) for k, v in text.MIRRORS.items()],
                subtitle=_("Checksums always come from Ubuntu's official servers."),
            )
            group.add(self.mirror)
            page.add(group)

        if self.windows:
            group = Adw.PreferencesGroup(
                title=_("Windows Account"),
                description=_("Windows is installed offline with this local account. No Microsoft account is needed."),
            )
            self.user = Adw.EntryRow(title=_("User Name"), text=login_name())
            self.password = Adw.PasswordEntryRow(title=_("Password"))
            self.confirm = Adw.PasswordEntryRow(title=_("Confirm Password"))
            for row in (self.user, self.password, self.confirm):
                row.connect("changed", lambda *_: self.validate())
                group.add(row)
            page.add(group)

        cpus, memory = util.resource_limits(self.snapshot)
        group = Adw.PreferencesGroup(title=_("Resources"))
        self.cpu = util.spin(_("Processors"), 1, cpus, 1, min(4 if self.windows else 2, cpus))
        minimum = 4608 if self.windows else 512
        self.memory_values = text.memory_choices(minimum, max(memory, minimum))
        self.memory = util.combo(_("Memory"), util.memory_items(self.memory_values))
        util.combo_select(self.memory, 4608 if self.windows else min(2048, self.memory_values[-1]))
        self.disk = util.spin(
            _("Disk Size (GB)"), 64 if self.windows else 8, 2048, 8, 64 if self.windows else 16,
            subtitle=self.free_space(),
        )
        for row in (self.cpu, self.memory, self.disk):
            group.add(row)
        page.add(group)

        self.gpu = None
        if not self.windows:
            group = Adw.PreferencesGroup(
                title=_("Display"),
                description=_("With graphics on, Ubuntu is set up with a desktop.") if kind == "cloud"
                else _("Graphics need a desktop installed in the image."),
            )
            modes = self.snapshot.modes(guest_os="linux")
            self.gpu = util.combo(_("Graphics"), [(m, text.graphics(m)) for m in modes])
            util.combo_select(self.gpu, "virgl" if "virgl" in modes else modes[0])
            self.gpu.connect("notify::selected", lambda *_: self.graphics_changed())
            self.graphics_changed()
            group.add(self.gpu)
            page.add(group)
        else:
            self.edition = Adw.EntryRow(title=_("Edition"), text="Professional")
            advanced = Adw.ExpanderRow(
                title=_("Advanced"), subtitle=_("Windows edition to install"),
            )
            advanced.add_row(self.edition)
            group = Adw.PreferencesGroup()
            group.add(advanced)
            page.add(group)
        self.set_name({"cloud": "ubuntu", "linux": "linux", "windows11": "windows"}[kind])
        self.validate()

    # Fields ---------------------------------------------------------------

    def free_space(self):
        store = self.window.store
        path = store if store.exists() else Path.home()
        try:
            free = shutil.disk_usage(path).free // 1024**3
        except OSError:
            return None
        return _("{size} GB free on this device").format(size=free)

    def taken(self, name):
        return (
            name in self.window.values
            or name in self.window.pending
            or (self.window.store / "vms" / name).exists()
        )

    def unique(self, base):
        name, n = base, 2
        while self.taken(name):
            suffix = f"-{n}"
            name, n = base[: 32 - len(suffix)] + suffix, n + 1
        return name

    def set_name(self, base):
        self.setting_name = True
        self.name.set_text(self.unique(base))
        self.setting_name = False

    def name_changed(self, *_):
        if not self.setting_name:
            self.name_edited = True
        self.validate()

    def graphics_changed(self):
        mode = util.combo_key(self.gpu)
        if mode:
            self.gpu.set_subtitle(text.graphics_help(mode))

    def choose_file(self):
        dialog = Gtk.FileDialog(
            title=_("Choose a Windows ISO") if self.windows else _("Choose a Disk Image"),
            modal=True,
        )
        wanted = Gtk.FileFilter(name=_("ISO Images") if self.windows else _("Disk Images"))
        for pattern in ("*.iso",) if self.windows else ("*.img", "*.raw", "*.qcow2"):
            wanted.add_pattern(pattern)
            wanted.add_pattern(pattern.upper())
        everything = Gtk.FileFilter(name=_("All Files"))
        everything.add_pattern("*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(wanted)
        filters.append(everything)
        dialog.set_filters(filters)
        dialog.set_default_filter(wanted)
        dialog.open(self.window, None, self.file_chosen)

    def file_chosen(self, dialog, result):
        try:
            chosen = dialog.open_finish(result)
        except GLib.Error:
            return
        if chosen is None or chosen.get_path() is None:
            return
        self.use_file(Path(chosen.get_path()))

    def use_file(self, path):
        problem = None
        try:
            kind = imageprobe.image_format(path)
        except (OSError, ValueError):
            kind = None
        if self.windows and kind != "iso":
            problem = _("This file isn't an ISO image.")
        elif not self.windows and kind not in ("raw", "qcow2"):
            problem = _("This file isn't a raw or qcow2 disk image.")
        self.path, self.file_ok = path, problem is None
        self.file.set_subtitle(problem or path.name)
        if problem:
            self.file.add_css_class("error")
        else:
            self.file.remove_css_class("error")
            if not self.name_edited and not self.windows:
                self.set_name(sanitize(path.stem))
        self.validate()

    def validate(self):
        name = self.name.get_text()
        problem = None
        if not NAME.fullmatch(name):
            problem = _("Use lowercase letters, digits and hyphens, starting with a letter.")
        elif self.taken(name):
            problem = _("A virtual machine with this name already exists.")
        self.name_group.set_description(problem if name else None)
        if problem and name:
            self.name.add_css_class("error")
        else:
            self.name.remove_css_class("error")
        ok = problem is None and self.file_ok
        if self.windows:
            password = self.password.get_text()
            confirm = self.confirm.get_text()
            mismatch = bool(confirm) and confirm != password
            if mismatch:
                self.confirm.add_css_class("error")
            else:
                self.confirm.remove_css_class("error")
            ok = (
                ok and bool(self.user.get_text().strip()) and bool(password)
                and confirm == password and len(password.encode()) <= 256
                and bool(self.snapshot.modes(guest_os="windows11"))
            )
        self.create_button.set_sensitive(ok)
        return ok

    # Create ---------------------------------------------------------------

    def create(self):
        if not self.validate():
            return
        name = self.name.get_text()
        gpu = "simplefb" if self.windows else util.combo_key(self.gpu)
        accelerated = gpu in ("2d", "virgl", "venus", "native")
        payload = not self.windows and (
            accelerated or "guest_kernel" in self.snapshot.runtime.get("components", {})
        )
        request = core.CreateRequest(
            store=self.window.store,
            name=name,
            os="windows11" if self.windows else "linux",
            image=str(self.path) if self.kind == "linux" else None,
            iso=self.path if self.windows else None,
            edition=(self.edition.get_text().strip() or None) if self.windows else None,
            local_user=self.user.get_text().strip() if self.windows else None,
            release=catalogue.entries()[0]["release"],
            mirror=util.combo_key(self.mirror) if self.kind == "cloud" else "official",
            cpus=int(self.cpu.get_value()),
            memory_mib=util.combo_key(self.memory),
            disk_gib=int(self.disk.get_value()),
            gpu=gpu,
            guest_payload=payload,
            vhost_net=False,
        )
        password = self.password.get_text() if self.windows else None
        if self.windows:
            self.password.set_text("")
            self.confirm.set_text("")
        self.dialog.close()
        self.window.create_vm(name, self.kind, request, password)
