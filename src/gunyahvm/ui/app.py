# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""GTK frontend; VM management, state and settings all go through the core API."""

from pathlib import Path
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk
from .. import core, capabilities, windows_vm, windows_agent
from ..monitor import StateMonitor
from . import util
from .settings import Settings
from .wizard import CreateWizard
from .hostinfo import HostDialog

APP_ID = "io.github.yzddmr6.GunyahVM"


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(
            application=app, title="GunyahVM", default_width=1100, default_height=760
        )
        self.store, self.selected, self.snapshot = (
            app.store,
            None,
            capabilities.Snapshot(),
        )
        self.rows = {}
        self.set_size_request(360, 480)
        self.toasts = Adw.ToastOverlay()
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.pack_start(
            util.button(
                icon="list-add-symbolic",
                tooltip="创建 / Create",
                cb=lambda: CreateWizard(self).present(),
            )
        )
        header.pack_end(
            util.button(
                icon="computer-symbolic",
                tooltip="宿主信息 / Host information",
                cb=lambda: HostDialog(self).present(),
            )
        )
        view.add_top_bar(header)
        self.banner = Adw.Banner(
            title="正在读取宿主能力 / Reading host capabilities", revealed=True
        )
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content.append(self.banner)
        self.listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.listbox.add_css_class("navigation-sidebar")
        self.listbox.connect("row-selected", self.select)
        scroll = Gtk.ScrolledWindow(
            vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER
        )
        scroll.set_child(self.listbox)
        sidebar = Adw.NavigationPage(title="虚拟机 / Virtual machines", child=scroll)
        self.stack = Gtk.Stack()
        self.stack.add_named(
            Adw.StatusPage(
                icon_name="computer-symbolic",
                title="GunyahVM",
                description="创建 Linux 或 Windows ARM64 虚拟机\nCreate a Linux or Windows ARM64 virtual machine",
            ),
            "empty",
        )
        self.detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.title = Gtk.Label(xalign=0, margin_start=16, margin_top=12)
        self.title.add_css_class("title-2")
        self.detail_box.append(self.title)
        self.state_label = Gtk.Label(
            xalign=0, wrap=True, margin_start=16, margin_end=16, selectable=True
        )
        self.detail_box.append(self.state_label)
        actions = Gtk.Box(spacing=8, margin_start=16, margin_end=16)
        self.start = util.button(
            label="启动 / Start",
            css=["suggested-action"],
            cb=lambda: self.action("start"),
        )
        self.stop = util.button(
            label="关机 / Shut down", cb=lambda: self.action("stop")
        )
        self.restart = util.button(
            label="重启 / Restart", cb=lambda: self.action("restart")
        )
        self.display = util.button(
            label="显示 / Display", cb=lambda: self.action("view")
        )
        menu = Gio.Menu()
        menu.append("强制断电 / Force off", "win.force")
        menu.append("删除 / Delete", "win.delete")
        menu.append("RDP", "win.rdp")
        for name in ("force", "delete", "rdp"):
            action = Gio.SimpleAction.new(name, None)
            action.connect(
                "activate",
                lambda _a, _p, n=name: (
                    self.confirm(n) if n != "rdp" else self.action(n)
                ),
            )
            self.add_action(action)
        for button in (
            self.start,
            self.stop,
            self.restart,
            self.display,
            Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu),
        ):
            actions.append(button)
        self.detail_box.append(actions)
        self.tabs = Gtk.Stack(vexpand=True)
        switcher = Gtk.StackSwitcher(stack=self.tabs, halign=Gtk.Align.CENTER)
        self.detail_box.append(switcher)
        self.settings_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.tabs.add_titled(self.settings_box, "settings", "设置 / Settings")
        self.console = Gtk.TextView(
            editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR
        )
        console_scroll = Gtk.ScrolledWindow(vexpand=True)
        console_scroll.set_child(self.console)
        self.tabs.add_titled(console_scroll, "console", "控制台 / Console")
        self.detail_box.append(self.tabs)
        self.stack.add_named(self.detail_box, "detail")
        page = Adw.NavigationPage(title="虚拟机详情 / VM details", child=self.stack)
        self.split = Adw.NavigationSplitView(sidebar=sidebar, content=page)
        self.split.set_min_sidebar_width(240)
        self.split.set_max_sidebar_width(300)
        content.append(self.split)
        self.split.set_vexpand(True)
        view.set_content(content)
        self.toasts.set_child(view)
        self.set_content(self.toasts)
        breakpoint = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 720sp")
        )
        breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint)
        self.monitor = StateMonitor(
            lambda states, n: GLib.idle_add(self.on_states, states),
            collect=self.collect,
        )
        self.monitor.start()
        self.connect("close-request", self.closed)
        self.refresh_host()

    def refresh_host(self):
        util.in_thread(capabilities.probe, self.host_loaded)

    def collect(self, _ids):
        values = core.list_vms(self.store)
        result = {v["config"]["name"]: v for v in values}
        if self.selected and self.selected in result:
            try:
                result[self.selected]["console"] = core.console_text(
                    self.store, self.selected
                )
            except (OSError, ValueError, RuntimeError):
                pass
        return result, sum(v["status"]["state"] == "running" for v in values)

    def host_loaded(self, snapshot, error):
        if error:
            self.banner.set_title(error)
        else:
            self.snapshot = snapshot
            self.banner.set_title(
                " / ".join(snapshot.errors.values()) or snapshot.profile["name"]
            )
            self.banner.set_revealed(
                bool(snapshot.errors) or not snapshot.device_access
            )
        if self.selected in self.rows:
            self.select(self.listbox, self.rows[self.selected])
        self.reload()

    def reload(self):
        self.monitor.kick()

    def closed(self, *_):
        self.monitor.stop()
        return False

    def on_states(self, values):
        if self.monitor.stopped:
            return False
        for name in list(self.rows):
            if name not in values:
                self.listbox.remove(self.rows.pop(name))
                if self.selected == name:
                    self.selected = None
                    self.stack.set_visible_child_name("empty")
        for name, value in values.items():
            if name not in self.rows:
                row = Adw.ActionRow(title=name)
                self.rows[name] = row
                self.listbox.append(row)
            row = self.rows[name]
            row.value = value
            row.set_subtitle(value["status"]["state"])
            if self.selected == name:
                self.update_state(value)
        return False

    def select(self, _box, row):
        if row is None:
            return
        value = row.value
        self.selected = value["config"]["name"]
        self.title.set_label(self.selected)
        self.stack.set_visible_child_name("detail")
        self.split.set_show_content(True)
        while child := self.settings_box.get_first_child():
            self.settings_box.remove(child)
        self.settings = None
        if value["status"]["state"] != "invalid":
            self.settings = Settings(value["config"], self.snapshot)
            self.settings.set_vexpand(True)
            self.settings_box.append(self.settings)
            self.save = util.button(
                label="保存设置 / Save settings", cb=self.save_settings
            )
            self.save.set_margin_bottom(12)
            self.settings_box.append(self.save)
        self.update_state(value)
        self.monitor.kick()

    def update_state(self, value):
        state, cfg = value["status"], value["config"]
        running = state["state"] in ("running", "starting", "stopping", "restarting")
        invalid = state["state"] == "invalid"
        self.state_label.set_label(
            state["state"]
            + (" · " + state["ipv4"] if state.get("ipv4") else "")
            + "\n"
            + (
                state.get("error")
                or state.get("stop_error")
                or state.get("guard_reason")
                or ""
            )
        )
        self.start.set_sensitive(not running and not invalid)
        self.stop.set_sensitive(running)
        self.restart.set_sensitive(running)
        self.display.set_sensitive(running and cfg.get("display", {}).get("vnc", False))
        if getattr(self, "settings", None):
            self.settings.set_sensitive(not running)
            self.save.set_sensitive(not running)
        if "console" in value:
            self.console.get_buffer().set_text(value["console"])

    def save_settings(self):
        try:
            cfg = self.settings.collect()
            core.save_config(self.store, self.selected, cfg)
            self.reload()
            self.toasts.add_toast(Adw.Toast(title="已保存 / Saved"))
        except (OSError, ValueError, RuntimeError) as exc:
            util.error(self, "无法保存 / Cannot save", str(exc))

    def confirm(self, action):
        util.alert(
            self,
            "确认 / Confirm",
            "强制断电可能丢失数据。" if action == "force" else "删除虚拟机及其磁盘？",
            responses=(("cancel", "取消 / Cancel"), ("yes", "继续 / Continue")),
            destructive="yes",
            callback=lambda answer: self.action(action) if answer == "yes" else None,
        )

    def action(self, action):
        if not self.selected:
            return
        name = self.selected

        def work():
            vm, cfg = core.owned_vm(self.store, name, core.os.getuid(), inputs=False)
            if action == "start":
                return core.start_vm(self.store, name)
            if action in ("stop", "force"):
                return core.stop_vm(vm, cfg, force=action == "force")
            if action == "restart":
                return core.restart_vm(self.store, name)
            if action == "delete":
                return core.delete_vm(self.store, name)
            if action == "view":
                return windows_vm.view(
                    vm, {**cfg, "vnc_port": cfg["display"]["vnc_port"]}
                )
            state = core.status(vm, cfg)
            if not state.get("ipv4"):
                raise ValueError("Guest has not reported an IP address")
            return core.subprocess.Popen(
                windows_agent.rdp_argv(state["ipv4"]), start_new_session=True
            )

        def done(result, error):
            self.get_application().release()
            self.reload()
            if error:
                util.error(self, "操作失败 / Action failed", error)

        self.get_application().hold()
        util.in_thread(work, done)


class App(Adw.Application):
    def __init__(self, store):
        super().__init__(
            application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )
        self.store, self.win = Path(store), None

    def do_activate(self):
        if not self.win:
            self.win = MainWindow(self)
        self.win.present()


def main(store):
    Adw.init()
    return App(store).run(["gunyahvm"])
