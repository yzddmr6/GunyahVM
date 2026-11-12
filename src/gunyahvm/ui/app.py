# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""GTK frontend; VM management, state and settings all go through the core API."""

import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk

from .. import __version__, capabilities, core, updates, windows_agent
from ..monitor import StateMonitor
from . import text, util
from .console import ConsoleDialog
from .hostinfo import SystemCheck
from .i18n import _
from .settings import Preferences, VmSettings
from .vmpage import VmPage
from .wizard import CreateDialog
from .snapshots import SnapshotDialog, clone_dialog
from .clipboard import ClipboardDialog

from . import APP_ID

# Actions that keep a VM busy until the backend call returns.
BUSY_ACTIONS = ("start", "stop", "restart", "force", "delete")


def failure_heading(action, name):
    return {
        "start": _("Couldn't Start “{name}”"),
        "stop": _("Couldn't Shut Down “{name}”"),
        "restart": _("Couldn't Restart “{name}”"),
        "force": _("Couldn't Force Off “{name}”"),
        "delete": _("Couldn't Delete “{name}”"),
        "view": _("Couldn't Open the Display"),
        "rdp": _("Couldn't Open Remote Desktop"),
    }[action].format(name=name)


def main_menu():
    menu = Gio.Menu()
    section = Gio.Menu()
    section.append(_("System Check"), "win.system-check")
    menu.append_section(None, section)
    section = Gio.Menu()
    section.append(_("_Preferences"), "win.preferences")
    section.append(_("_About GunyahVM"), "win.about")
    menu.append_section(None, section)
    return Gtk.MenuButton(
        icon_name="open-menu-symbolic", menu_model=menu, primary=True,
        tooltip_text=_("Main Menu"),
    )


def new_button():
    return Gtk.Button(
        icon_name="list-add-symbolic", action_name="win.new-vm",
        tooltip_text=_("New Virtual Machine"),
    )


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(
            application=app, title="GunyahVM", default_width=1200, default_height=820
        )
        self.set_size_request(360, 520)
        self.store = app.store
        self.snapshot = capabilities.Snapshot()
        self.host_ready = True
        self.values = {}
        self.pending = {}
        self.order = []
        self.items = {}
        self.selected = None
        self.busy = {}
        self.syncing = False
        self.console = None
        self.settings = None
        self.update_release = None
        self.update_pending = False
        self.update_dismissed = False
        self.displays = {}
        self.display_hold = False
        self.setup_actions()

        self.host_banners, self.update_banners = [], []
        self.main = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.main.add_named(self.build_empty(), "empty")
        self.main.add_named(self.build_split(), "split")
        self.toasts = Adw.ToastOverlay(child=self.main)
        self.set_content(self.toasts)

        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 640sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        breakpoint.connect("apply", lambda *_: self.sidebar.set_mode(Adw.SidebarMode.PAGE))
        breakpoint.connect("unapply", lambda *_: self.sidebar.set_mode(Adw.SidebarMode.SIDEBAR))
        self.add_breakpoint(breakpoint)

        self.monitor = StateMonitor(
            lambda states, _n: GLib.idle_add(self.on_states, states), collect=self.collect
        )
        self.monitor.start()
        self.connect("close-request", self.closed)
        self.refresh_host()

    # Layout ---------------------------------------------------------------

    def banners(self, host_problems):
        """Update notice, plus the host notice where no VM page explains it."""
        update = Adw.Banner(button_label=_("_View"))
        update.connect("button-clicked", self.open_release)
        self.update_banners.append(update)
        if not host_problems:
            return (update,)
        host = Adw.Banner(
            title=_("This device can't run virtual machines yet"),
            button_label=_("_Details"),
        )
        host.set_action_name("win.system-check")
        self.host_banners.append(host)
        return host, update

    def build_empty(self):
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.pack_start(new_button())
        header.pack_end(main_menu())
        view.add_top_bar(header)
        for banner in self.banners(host_problems=True):
            view.add_top_bar(banner)
        create = util.button(
            label=_("_Create Virtual Machine"), css=("pill", "suggested-action"),
        )
        create.set_action_name("win.new-vm")
        create.set_halign(Gtk.Align.CENTER)
        self.empty = Adw.StatusPage(
            icon_name=APP_ID, title=_("No Virtual Machines"), child=create
        )
        view.set_content(self.empty)
        return view

    def build_split(self):
        self.sidebar = Adw.Sidebar()
        self.section = Adw.SidebarSection()
        self.sidebar.append(self.section)
        self.sidebar.connect("notify::selected", self.on_sidebar_selected)
        self.sidebar.connect("activated", self.on_sidebar_activated)
        side = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.pack_start(new_button())
        header.pack_end(main_menu())
        side.add_top_bar(header)
        side.set_content(self.sidebar)

        self.page = VmPage(self)
        self.creating = Adw.StatusPage()
        self.creating.set_paintable(Adw.SpinnerPaintable(widget=self.creating))
        self.failed = Adw.StatusPage(icon_name="dialog-error-symbolic")
        self.failed_key = None
        self.nothing = Adw.StatusPage(
            icon_name="computer-symbolic", title=_("No Virtual Machine Selected")
        )
        self.content = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        for name, child in (
            ("vm", self.page), ("creating", self.creating),
            ("failed", self.failed), ("nothing", self.nothing),
        ):
            self.content.add_named(child, name)
        view = Adw.ToolbarView()
        self.content_header = Adw.HeaderBar(show_title=False)
        self.vm_menu = Gtk.MenuButton(
            icon_name="view-more-symbolic", menu_model=self.page.menu(),
            tooltip_text=_("More"),
        )
        self.settings_button = Gtk.Button(
            icon_name="emblem-system-symbolic", action_name="win.settings",
            tooltip_text=_("Settings"),
        )
        self.content_header.pack_end(self.vm_menu)
        self.content_header.pack_end(self.settings_button)
        back = Gtk.Button(icon_name="go-previous-symbolic", tooltip_text=_("Overview"))
        back.connect("clicked", lambda *_: self.content.set_visible_child_name("vm"))
        self.content_header.pack_start(back)
        fullscreen = Gtk.Button(icon_name="view-fullscreen-symbolic", tooltip_text=_("Full Screen"))
        fullscreen.connect("clicked", lambda *_: self.unfullscreen() if self.is_fullscreen() else self.fullscreen())
        self.content_header.pack_end(fullscreen)
        view.add_top_bar(self.content_header)
        for banner in self.banners(host_problems=False):
            view.add_top_bar(banner)
        view.set_content(self.content)

        self.content_page = Adw.NavigationPage(title="GunyahVM", child=view, tag="content")
        self.split = Adw.NavigationSplitView(
            sidebar=Adw.NavigationPage(title="GunyahVM", child=side, tag="sidebar"),
            content=self.content_page,
            min_sidebar_width=260, max_sidebar_width=320,
        )
        self.split.connect("notify::show-content", self.on_show_content)
        return self.split

    def setup_actions(self):
        for name, callback in (
            ("new-vm", lambda: CreateDialog(self).present(self)),
            ("system-check", lambda: SystemCheck(self).present(self)),
            ("preferences", lambda: Preferences(self).present(self)),
            ("about", self.about),
            ("start", lambda: self.run(self.selected, "start")),
            ("shutdown", lambda: self.shut_down(self.selected)),
            ("restart", lambda: self.run(self.selected, "restart")),
            ("force-off", lambda: self.confirm_force_off(self.selected)),
            ("delete", lambda: self.confirm_delete(self.selected)),
            ("display", lambda: self.run(self.selected, "view")),
            ("rdp", lambda: self.run(self.selected, "rdp")),
            ("console", self.open_console),
            ("settings", self.open_settings),
            ("copy-ssh", self.copy_ssh),
            ("setup-details", lambda: self.setup_details(self.selected)),
            ("snapshots", lambda: SnapshotDialog(self).present(self)),
            ("clone", lambda: clone_dialog(self)),
            ("clipboard", lambda: ClipboardDialog(self).present(self)),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, cb=callback: cb())
            self.add_action(action)
        select = Gio.SimpleAction.new("select", GLib.VariantType.new("s"))
        select.connect("activate", lambda _a, value: self.select(value.get_string(), show=True))
        self.add_action(select)

    # Host and updates -----------------------------------------------------

    def refresh_host(self):
        util.in_thread(capabilities.probe, self.host_loaded)

    def host_loaded(self, snapshot, error):
        if error:
            self.host_ready = False
        else:
            self.snapshot = snapshot
            self.host_ready = snapshot.device_access and snapshot.features()["headless"]
        for banner in self.host_banners:
            banner.set_revealed(not self.host_ready)
        self.sync()
        return False

    def check_updates(self):
        if self.update_pending or not updates.enabled():
            return
        self.update_pending = True
        util.in_thread(updates.check, self.update_loaded)

    def update_loaded(self, release, error):
        self.update_pending = False
        if self.monitor.stopped or not updates.enabled() or error or not release:
            return False
        self.update_release = release
        for banner in self.update_banners:
            banner.set_title(
                _("GunyahVM {version} is available").format(version=release["version"])
            )
            banner.set_revealed(not self.update_dismissed)
        return False

    def hide_update(self):
        for banner in self.update_banners:
            banner.set_revealed(False)

    def open_release(self, *_):
        if not self.update_release:
            return
        try:
            Gio.AppInfo.launch_default_for_uri(self.update_release["url"], None)
        except GLib.Error as exc:
            util.error(self, _("Couldn't Open the Release Page"), str(exc))
            return
        self.update_dismissed = True
        self.hide_update()

    # State ----------------------------------------------------------------

    def collect(self, _ids):
        values = core.list_vms(self.store)
        result = {v["config"]["name"]: v for v in values}
        dialog = self.console  # read once: the main loop may close it meanwhile
        console = dialog.name if dialog else None
        if console in result:
            try:
                result[console]["console"] = core.console_text(self.store, console)
            except (OSError, ValueError, RuntimeError):
                pass
        return result, sum(v["status"]["state"] == "running" for v in values)

    def reload(self):
        self.monitor.kick()

    def closed(self, *_):
        if self.display_hold or self.busy:
            self.set_visible(False)
            return True
        self.monitor.stop()
        for display in self.displays.values():
            self.content.remove(display)
            display.dispose_display()
        self.displays.clear()
        return False

    def on_states(self, values):
        if self.monitor.stopped:
            return False
        self.values = values
        active_display = any(n in self.displays and v["status"]["state"] in text.ACTIVE
                             for n, v in values.items())
        if active_display != self.display_hold:
            self.display_hold = active_display
            if active_display:
                self.get_application().hold()
            else:
                self.get_application().release()
        for name in [n for n, p in self.pending.items() if p.get("done") and n in values]:
            del self.pending[name]
        self.sync()
        self.get_application().show_requested_display()
        return False

    def names(self):
        return sorted(set(self.values) | set(self.pending))

    def sync(self):
        names = self.names()
        self.syncing = True
        try:
            if names != self.order:
                self.section.remove_all()
                self.items = {}
                for name in names:
                    item = Adw.SidebarItem(title=name, icon_name="computer-symbolic")
                    item.kind = None
                    self.section.append(item)
                    self.items[name] = item
                self.order = names
            for name in names:
                self.update_item(name)
            if self.selected not in names:
                self.selected = None
                if names and not self.split.get_collapsed():
                    self.selected = names[0]
                elif self.split.get_collapsed():
                    self.split.set_show_content(False)
            self.sidebar.set_selected(
                names.index(self.selected) if self.selected in names
                else Gtk.INVALID_LIST_POSITION
            )
        finally:
            self.syncing = False
        if names:
            self.main.set_visible_child_name("split")
        else:
            windows = self.snapshot.features()["windows"]
            self.empty.set_description(
                _("Run Ubuntu or Windows 11 alongside this device's system.") if windows
                else _("Run Ubuntu or another Linux system alongside this device's system.")
            )
            self.main.set_visible_child_name("empty")
        self.update_page()
        self.update_actions()
        if self.console and self.console.name in self.values:
            value = self.values[self.console.name]
            self.console.set_text(value.get("console"), value["status"]["state"])
        if self.settings and self.settings.name in self.values:
            state = self.values[self.settings.name]["status"]["state"]
            self.settings.set_running(state in text.ACTIVE)

    def status_of(self, name):
        """(subtitle, kind) for the sidebar and page header."""
        if name in self.pending:
            if self.pending[name].get("error"):
                return _("Creation failed"), "error"
            return _("Creating…"), "busy"
        status = self.values[name]["status"]
        if name in self.busy:
            return text.busy(self.busy[name]), "busy"
        return text.state(status)

    def update_item(self, name):
        item = self.items[name]
        subtitle, kind = self.status_of(name)
        item.set_subtitle(subtitle)
        if kind != item.kind:
            item.kind = kind
            if kind == "busy":
                item.set_suffix(Adw.Spinner())
            elif kind in ("running", "warning", "error"):
                dot = Gtk.Box(valign=Gtk.Align.CENTER)
                dot.add_css_class("status-dot")
                dot.add_css_class(kind)
                item.set_suffix(dot)
            else:
                item.set_suffix(None)

    def context(self, name):
        other = next(
            (n for n, v in sorted(self.values.items())
             if n != name and v["status"]["state"] in text.ACTIVE),
            None,
        )
        return {"other": other, "host_ready": self.host_ready, "busy": self.busy.get(name)}

    def update_page(self):
        name = self.selected
        self.content_page.set_title(name or "GunyahVM")
        vm = name in self.values and name not in self.pending
        controls = vm and self.values[name]["status"]["state"] != "requires-update"
        self.settings_button.set_visible(controls)
        self.vm_menu.set_visible(controls)
        if name in self.pending:
            pending = self.pending[name]
            if pending.get("error"):
                self.show_failed(name, pending["error"])
            else:
                self.creating.set_title(_("Creating “{name}”…").format(name=name))
                self.creating.set_description({
                    "cloud": _("Downloading Ubuntu and preparing its disk. This can take several minutes."),
                    "linux": _("Copying the disk image. This can take a few minutes."),
                    "windows11": _("Preparing the Windows installation. This can take several minutes."),
                }[pending["kind"]])
                self.content.set_visible_child_name("creating")
        elif vm:
            self.page.update(self.values[name], self.context(name))
            if not (self.content.get_visible_child_name() == "display-" + name
                    and (self.values[name]["status"]["state"] in text.ACTIVE or name in self.busy)):
                self.content.set_visible_child_name("vm")
        else:
            self.content.set_visible_child_name("nothing")

    def show_failed(self, name, error):
        self.content.set_visible_child_name("failed")
        if self.failed_key == (name, error):
            return  # keep the Details expander as the user left it
        self.failed_key = (name, error)
        self.failed.set_title(_("Couldn't Create “{name}”").format(name=name))
        self.failed.set_description(text.explain(util.clean(error)) or _("Something went wrong."))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, halign=Gtk.Align.CENTER)
        expander = util.details(error)
        expander.set_size_request(360, -1)
        box.append(expander)
        remove = util.button(label=_("_Remove"), css=("pill",))
        remove.set_halign(Gtk.Align.CENTER)
        remove.connect("clicked", lambda *_: self.forget_pending(name))
        box.append(remove)
        self.failed.set_child(box)

    def forget_pending(self, name):
        self.pending.pop(name, None)
        self.selected = None
        self.sync()

    def update_actions(self):
        value = self.values.get(self.selected) if self.selected not in self.pending else None
        status = value["status"] if value else {}
        cfg = value["config"] if value else {}
        state = status.get("state")
        busy = self.selected in self.busy
        running = state == "running"
        active = state in text.ACTIVE
        windows = cfg.get("os") == "windows11"
        display = cfg.get("display", {})
        other = self.context(self.selected)["other"] if value else None
        usable = bool(value) and state not in ("invalid", "requires-update")
        enabled = {
            "start": usable and not busy and not active
            and not other and self.host_ready,
            "shutdown": running and not busy,
            "restart": running and not busy and not text.starting(cfg, status),
            "force-off": active or state == "interrupted",
            "delete": usable and not busy and not active,
            "display": running and display.get("gpu") != "none",
            "rdp": running and windows and bool(status.get("ipv4")),
            "console": usable,
            "settings": usable,
            "copy-ssh": bool(value) and cfg.get("os") == "linux" and bool(cfg.get("ssh_public_key")),
            "setup-details": bool(value) and bool(text.setup_result(status)),
            "snapshots": usable and not busy and not active,
            "clone": usable and not busy and not active and not cfg.get("install_media"),
            "clipboard": bool(value) and not busy and active,
        }
        for name, on in enabled.items():
            self.lookup_action(name).set_enabled(bool(on))

    # Selection ------------------------------------------------------------

    def on_sidebar_selected(self, *_):
        if self.syncing:
            return
        index = self.sidebar.get_selected()
        if 0 <= index < len(self.order):
            self.select(self.order[index])

    def on_sidebar_activated(self, _sidebar, index):
        if 0 <= index < len(self.order):
            self.select(self.order[index], show=True)

    def on_show_content(self, *_):
        if self.split.get_collapsed() and not self.split.get_show_content():
            self.selected = None
            self.syncing = True
            self.sidebar.set_selected(Gtk.INVALID_LIST_POSITION)
            self.syncing = False

    def select(self, name, show=False):
        if name not in self.names():
            return
        self.selected = name
        self.syncing = True
        self.sidebar.set_selected(self.order.index(name) if name in self.order else Gtk.INVALID_LIST_POSITION)
        self.syncing = False
        self.update_page()
        self.update_actions()
        if show:
            self.split.set_show_content(True)

    # Actions --------------------------------------------------------------

    def ensure_display(self, name, cfg):
        from .display import VmDisplay

        width, height = cfg["display"]["width"], cfg["display"]["height"]
        if name not in self.displays:
            display = VmDisplay(cfg["id"], width, height)
            self.content.add_named(display, "display-" + name)
            self.displays[name] = display
        display = self.displays[name]
        display.guest_width, display.guest_height = width, height
        display.queue_resize()
        return display

    def run(self, name, action):
        if name not in self.values or self.values[name]["status"]["state"] == "requires-update":
            return
        display_socket = None
        reconnect = False
        if action in ("start", "restart", "view"):
            cfg = self.values[name]["config"]
            if cfg["display"]["gpu"] != "none":
                try:
                    if action == "view" and cfg["display"]["vnc"]:
                        raise ValueError("Restart this VM to switch from VNC to the embedded display")
                    reconnect = action == "view" and name not in self.displays
                    display = self.ensure_display(name, cfg)
                    self.content.set_visible_child(display)
                    display.focus()
                    if action == "start" and cfg["display"].get("fullscreen"):
                        self.fullscreen()
                    display_socket = display.socket_path
                except Exception as exc:
                    util.error(self, _("Couldn't Open the Display"), str(exc))
                    return
                if action == "view" and not reconnect:
                    return
        if action in BUSY_ACTIONS:
            self.busy[name] = action
            self.sync()

        def work():
            vm, cfg = core.owned_vm(self.store, name, os.getuid(), inputs=False)
            if action == "start":
                return core.start_vm(self.store, name, display_socket=display_socket)
            if action in ("stop", "force"):
                return core.stop_vm(vm, cfg, force=action == "force")
            if action == "restart":
                return core.restart_vm(self.store, name, display_socket=display_socket)
            if action == "delete":
                return core.delete_vm(self.store, name)
            if action == "view":
                return core.reconnect_display(vm, cfg, display_socket)
            state = core.status(vm, cfg)
            if not state.get("ipv4"):
                raise ValueError("Guest has not reported an IP address")
            return core.subprocess.Popen(
                windows_agent.rdp_argv(state["ipv4"]), start_new_session=True
            )

        def done(_result, error):
            self.get_application().release()
            self.busy.pop(name, None)
            if action == "delete" and not error:
                if self.selected == name:
                    self.selected = None
                self.toasts.add_toast(
                    Adw.Toast(title=_("“{name}” was deleted").format(name=name))
                )
            self.sync()
            self.reload()
            if action == "start" and not error and name in self.displays:
                self.content.set_visible_child(self.displays[name])
            if error:
                if reconnect and name in self.displays:
                    failed = self.displays.pop(name)
                    self.content.remove(failed)
                    failed.dispose_display()
                util.error(self, failure_heading(action, name), error)
            return False

        self.get_application().hold()
        util.in_thread(work, done)

    def shut_down(self, name):
        value = self.values.get(name)
        if value and text.starting(value["config"], value["status"]):
            # The backend would refuse: only a booted guest can power itself off.
            util.alert(
                self,
                _("“{name}” Is Still Starting").format(name=name),
                _("It can shut down cleanly once its system has finished starting. The first start "
                  "installs software; with a desktop this can take 20 minutes or more. Try again later, "
                  "or force it off and lose unsaved work in the virtual machine."),
                responses=(("cancel", _("_Cancel")), ("force", _("_Force Off"))),
                destructive="force",
                callback=lambda answer: self.run(name, "force") if answer == "force" else None,
            )
            return
        self.run(name, "stop")

    def setup_details(self, name):
        value = self.values.get(name)
        problem = value and text.setup_result(value["status"])
        if not problem:
            return
        util.alert(
            self,
            _("Setup Problems in “{name}”").format(name=name),
            _("These first-start steps didn't finish. The console has the full log."),
            responses=(("close", _("_Close")), ("console", _("_Console"))),
            default="close",
            callback=lambda answer: self.open_console() if answer == "console" else None,
            extra=util.text_block("\n\n".join(problem[1]) or _("No details were reported.")),
        )

    def confirm_force_off(self, name):
        util.alert(
            self,
            _("Force Off “{name}”?").format(name=name),
            _("This is like pulling the power plug. Unsaved work in the virtual machine will be lost."),
            responses=(("cancel", _("_Cancel")), ("force", _("_Force Off"))),
            destructive="force",
            callback=lambda answer: self.run(name, "force") if answer == "force" else None,
        )

    def confirm_delete(self, name):
        cfg = self.values[name]["config"]
        util.alert(
            self,
            _("Delete “{name}”?").format(name=name),
            _("The virtual machine and its {size} disk will be permanently deleted.").format(
                size=text.disk(cfg["disk_gib"])
            ),
            responses=(("cancel", _("_Cancel")), ("delete", _("_Delete"))),
            destructive="delete",
            callback=lambda answer: self.run(name, "delete") if answer == "delete" else None,
        )

    def open_console(self):
        if self.selected not in self.values:
            return
        if self.console:
            self.console.close()
        self.console = ConsoleDialog(self.selected)
        self.console.connect("closed", self.console_closed)
        self.console.present(self)
        self.reload()

    def console_closed(self, dialog):
        if self.console is dialog:
            self.console = None

    def open_settings(self):
        if self.selected not in self.values:
            return
        value = self.values[self.selected]
        self.settings = VmSettings(self, value)
        self.settings.connect("closed", self.settings_closed)
        self.settings.set_running(value["status"]["state"] in text.ACTIVE)
        self.settings.present(self)

    def settings_closed(self, dialog):
        if self.settings is dialog:
            self.settings = None

    def copy_ssh(self):
        if self.selected not in self.values:
            return
        command = "gunyahvm ssh " + self.selected
        util.copy(self, command)
        self.toasts.add_toast(Adw.Toast(title=_("Copied “{command}”").format(command=command)))

    def create_vm(self, name, kind, request, password):
        """Run creation in the background; the sidebar shows it until the VM exists."""
        self.pending[name] = {"kind": kind}
        self.selected = name
        self.sync()
        self.split.set_show_content(True)
        app = self.get_application()
        app.hold()

        def work():
            if password is None:
                return core.create(request)
            reader, writer = os.pipe()
            try:
                with os.fdopen(writer, "wb") as stream:
                    stream.write((password + "\n").encode())
                request.password_fd = reader
                return core.create(request)
            finally:
                os.close(reader)

        def done(_result, error):
            app.release()
            if error:
                self.pending[name]["error"] = error
            else:
                self.pending[name]["done"] = True
                self.toasts.add_toast(
                    Adw.Toast(title=_("“{name}” is ready").format(name=name))
                )
            self.sync()
            self.reload()
            return False

        util.in_thread(work, done)

    def about(self):
        about = Adw.AboutDialog(
            application_name="GunyahVM",
            application_icon=APP_ID,
            version=__version__,
            developer_name="yzddMr6",
            comments=_("Linux and Windows virtual machines on Qualcomm Gunyah."),
            website="https://github.com/yzddmr6/GunyahVM",
            issue_url="https://github.com/yzddmr6/GunyahVM/issues",
            license_type=Gtk.License.GPL_2_0_ONLY,
            copyright="© 2026 yzddMr6",
        )
        about.add_acknowledgement_section(
            _("Open Source Projects"),
            [
                "iceland-gvm (mzakamin-hub)",
                "DroidVM (Droid-VM)",
                "crosvm, minijail (ChromiumOS Authors)",
                "edk2, edk2-gunyah",
                "virtio-win (Red Hat)",
            ],
        )
        about.present(self)


class App(Adw.Application):
    def __init__(self, store, service=False):
        flags = Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        if service:
            flags |= Gio.ApplicationFlags.IS_SERVICE
        super().__init__(application_id=APP_ID, flags=flags)
        self.prepared_display = None
        self.store, self.win = Path(store), None
        self.requested_display = None

    def do_command_line(self, command_line):
        import argparse

        parser = argparse.ArgumentParser(add_help=False, exit_on_error=False)
        parser.add_argument("--store", type=Path, default=self.store)
        group = parser.add_mutually_exclusive_group()
        group.add_argument("--view")
        group.add_argument("--prepare-display")
        try:
            args, extra = parser.parse_known_args(command_line.get_arguments()[1:])
            if self.win is None and self.get_flags() & Gio.ApplicationFlags.IS_SERVICE:
                self.store = args.store.resolve()
            if extra or args.store.resolve() != self.store.resolve():
                raise ValueError("The open GunyahVM application uses another VM store")
            if args.prepare_display:
                name = core.vm_name(args.prepare_display)
                _, cfg = core.owned_vm(self.store, name, os.getuid(), inputs=False)
                self.activate()
                self.win.ensure_display(name, cfg)
                self.prepared_display = name
                self.win.reload()
                return 0
            if args.view:
                self.requested_display = core.vm_name(args.view)
        except (argparse.ArgumentError, argparse.ArgumentTypeError, OSError, RuntimeError, ValueError) as exc:
            command_line.printerr_literal(str(exc) + "\n")
            return 1
        self.activate()
        if self.requested_display:
            self.win.reload()
        return 0

    def show_requested_display(self):
        prepared = self.prepared_display
        if prepared in self.win.values:
            state = self.win.values[prepared]["status"]["state"]
            if state == "running":
                self.win.select(prepared, show=True)
                self.win.content.set_visible_child(self.win.displays[prepared])
                self.win.displays[prepared].focus()
                self.prepared_display = None
            elif state == "failed":
                self.prepared_display = None
        name = self.requested_display
        if not name:
            return
        self.requested_display = None
        if name not in self.win.values:
            util.error(self.win, _("Couldn't Open the Display"), "No such VM: " + name)
            return
        self.win.select(name, show=True)
        self.win.run(name, "view")

    def do_startup(self):
        Adw.Application.do_startup(self)
        util.install_css()
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.win.close() if self.win else self.quit())
        self.add_action(quit_action)
        for action, accels in (
            ("app.quit", ["<Control>q"]),
            ("win.new-vm", ["<Control>n"]),
            ("win.preferences", ["<Control>comma"]),
            ("window.close", ["<Control>w"]),
        ):
            self.set_accels_for_action(action, accels)

    def do_activate(self):
        created = self.win is None
        if created:
            self.win = MainWindow(self)
        self.win.present()
        if created:
            self.win.check_updates()


def main(store, view=None, service=False):
    Adw.init()
    if service:
        return App(store, service=True).run(["gunyahvm"])
    args = ["gunyahvm", "--store", str(Path(store).resolve())]
    if view:
        args += ["--view", view]
    return App(store).run(args)
