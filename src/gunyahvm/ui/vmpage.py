# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The selected virtual machine: identity, state-driven actions, notices and overview."""

from gi.repository import Adw, Gio, GLib, Gtk

from . import text, util
from .i18n import _


def property_row(title, copy=None, window=None):
    row = Adw.ActionRow(title=title, subtitle_selectable=True)
    row.add_css_class("property")
    if copy:
        row.add_suffix(util.icon_button("edit-copy-symbolic", copy, lambda: window.activate_action(
            "win.copy-ssh", None)))
    return row


class VmPage(Gtk.ScrolledWindow):
    def __init__(self, window):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.window = window
        self.notice_key = None
        self.primary_action = self.secondary_action = None
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=24,
            margin_top=32, margin_bottom=32, margin_start=16, margin_end=16,
        )
        clamp = Adw.Clamp(maximum_size=720, tightening_threshold=560, child=box)
        self.set_child(clamp)

        hero = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.tile = Gtk.Box(halign=Gtk.Align.CENTER)
        self.tile.add_css_class("vm-tile")
        self.tile.append(Gtk.Image(
            # hexpand centres the icon; vexpand would propagate and stretch the tile in tall windows
            icon_name="computer-symbolic", pixel_size=52, hexpand=True,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
        ))
        hero.append(self.tile)
        self.title = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER, margin_top=6)
        self.title.add_css_class("title-1")
        hero.append(self.title)
        status = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.dot.add_css_class("status-dot")
        self.spinner = Adw.Spinner(visible=False)
        self.status = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.status.add_css_class("dim-label")
        for child in (self.dot, self.spinner, self.status):
            status.append(child)
        hero.append(status)
        box.append(hero)

        actions = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        buttons = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, homogeneous=True)
        self.primary = Gtk.Button()
        self.secondary = Gtk.Button()
        for button in (self.primary, self.secondary):
            button.add_css_class("pill")
            button.set_size_request(180, -1)
            buttons.append(button)
        self.primary.connect("clicked", lambda *_: self.trigger(self.primary_action))
        self.secondary.connect("clicked", lambda *_: self.trigger(self.secondary_action))
        actions.append(buttons)
        self.caption = Gtk.Label(wrap=True, justify=Gtk.Justification.CENTER)
        self.caption.add_css_class("dim-label")
        self.caption.add_css_class("caption")
        actions.append(self.caption)
        box.append(actions)

        self.notices = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.notices.add_css_class("boxed-list")
        box.append(self.notices)

        self.overview = Adw.PreferencesGroup(title=_("Overview"))
        self.rows = {}
        for key, title in (
            ("system", _("System")),
            ("resources", _("Resources")),
            ("graphics", _("Graphics")),
            ("network", _("Network")),
            ("forwards", _("Port Forwarding")),
            ("shares", _("Shared Folders")),
        ):
            self.rows[key] = property_row(title)
            self.overview.add(self.rows[key])
        self.rows["ssh"] = property_row(_("SSH Login"), copy=_("Copy Command"), window=window)
        self.overview.add(self.rows["ssh"])
        box.append(self.overview)

    def menu(self):
        menu = Gio.Menu()
        power = Gio.Menu()
        power.append(_("_Restart"), "win.restart")
        power.append(_("_Force Off…"), "win.force-off")
        menu.append_section(None, power)
        tools = Gio.Menu()
        for label, action in (
            (_("Remote _Desktop"), "win.rdp"),
            (_("_Console"), "win.console"),
            (_("Copy SSH _Command"), "win.copy-ssh"),
        ):
            item = Gio.MenuItem.new(label, action)
            if action != "win.console":
                item.set_attribute_value("hidden-when", GLib.Variant.new_string("action-disabled"))
            tools.append_item(item)
        menu.append_section(None, tools)
        danger = Gio.Menu()
        danger.append(_("_Delete…"), "win.delete")
        menu.append_section(None, danger)
        return menu

    def trigger(self, action):
        if action:
            self.window.activate_action("win." + action, None)

    @staticmethod
    def set_button(button, icon, label, suggested=False, busy=False):
        look = (icon, label, suggested, busy)
        if getattr(button, "look", None) == look:
            return  # rebuilding would restart the spinner on every refresh
        button.look = look
        if busy:
            content = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
            content.append(Adw.Spinner())
            content.append(Gtk.Label(label=label))
        else:
            content = Adw.ButtonContent(icon_name=icon, label=label, use_underline=True)
        button.set_child(content)
        if suggested:
            button.add_css_class("suggested-action")
        else:
            button.remove_css_class("suggested-action")

    def update(self, value, context):
        cfg, status = value["config"], value["status"]
        state = status.get("state")
        busy = context["busy"]
        windows = cfg.get("os") == "windows11"
        display = cfg.get("display", {})
        label, kind = text.state(status)
        if busy:
            label, kind = text.busy(busy), "busy"
        if state == "running" and status.get("ipv4"):
            label = label + " · " + status["ipv4"]
        elif not busy and text.starting(cfg, status):
            label = label + " · " + _("Starting up")

        self.title.set_label(cfg["name"])
        self.status.set_label(label)
        self.dot.set_visible(kind != "busy")
        self.spinner.set_visible(kind == "busy")
        for name in ("running", "warning", "error"):
            if kind == name:
                self.dot.add_css_class(name)
            else:
                self.dot.remove_css_class(name)
        if state in text.ACTIVE:
            self.tile.add_css_class("running")
        else:
            self.tile.remove_css_class("running")

        self.primary_action = self.secondary_action = None
        self.secondary.set_visible(False)
        self.primary.set_visible(True)
        self.primary.set_sensitive(True)
        caption = ""
        if state == "invalid":
            self.primary.set_visible(False)
        elif busy or state in text.BUSY:
            self.set_button(self.primary, None, label.split(" · ")[0], busy=True)
            self.primary.set_sensitive(False)
        elif state == "running":
            if windows or display.get("vnc"):
                self.set_button(self.primary, "video-display-symbolic", _("Open _Display"), True)
                self.primary_action = "display"
                self.set_button(self.secondary, "system-shutdown-symbolic", _("_Shut Down"))
                self.secondary_action = "shutdown"
                self.secondary.set_visible(True)
            elif display.get("gpu") == "none":
                self.set_button(self.primary, "utilities-terminal-symbolic", _("_Console"), True)
                self.primary_action = "console"
                self.set_button(self.secondary, "system-shutdown-symbolic", _("_Shut Down"))
                self.secondary_action = "shutdown"
                self.secondary.set_visible(True)
                caption = _("No display is configured. Use the console or SSH.")
            else:
                self.set_button(self.primary, "system-shutdown-symbolic", _("_Shut Down"))
                self.primary_action = "shutdown"
                caption = _("The display opens in its own window.")
        else:
            self.set_button(self.primary, "media-playback-start-symbolic", _("_Start"), True)
            self.primary_action = "start"
            self.primary.set_sensitive(
                not context["other"] and context["host_ready"]
            )
        self.caption.set_label(caption)
        self.caption.set_visible(bool(caption))

        self.update_notices(cfg, status, context)
        self.update_overview(cfg, status)

    def update_notices(self, cfg, status, context):
        state = status.get("state")
        notices = []
        error = util.clean(status.get("error") or "")
        if state == "invalid":
            notices.append((
                "dialog-error-symbolic", "error", _("This virtual machine can't be used"),
                text.explain(error) or _("Its configuration couldn't be read."), error, None,
            ))
        elif state not in text.ACTIVE:
            if not context["host_ready"]:
                notices.append((
                    "dialog-warning-symbolic", "warning",
                    _("This device can't run virtual machines yet"),
                    _("Open System Check to see what is missing."), "",
                    (_("System Check"), "win.system-check"),
                ))
            elif context["other"]:
                notices.append((
                    "dialog-information-symbolic", "accent",
                    _("“{name}” is running").format(name=context["other"]),
                    _("Only one virtual machine can run at a time. Shut it down to start this one."),
                    "", (_("Show"), "win.select::" + context["other"]),
                ))
        guard = status.get("guard_reason") or ""
        if guard and state in ("stopped", "failed", "stopping"):
            notices.append((
                "dialog-warning-symbolic", "warning",
                _("Shut down to protect the device"),
                text.explain(guard) or guard, guard if text.explain(guard) else "", None,
            ))
        elif state in ("failed", "interrupted") and not status.get("forced_stop"):
            notices.append((
                "dialog-error-symbolic", "error",
                _("The virtual machine stopped unexpectedly"),
                text.explain(error) or _("The console may show what happened."),
                error, (_("Console"), "win.console"),
            ))
        problem = text.setup_result(status)
        if problem:
            level, problems = problem
            notices.append((
                "dialog-warning-symbolic", "warning" if level == "error" else "accent",
                _("Started with setup errors") if level == "error" else _("Started with setup warnings"),
                _("Some first-start steps failed, so parts of the system, such as the desktop, may be missing.")
                if level == "error" else _("First-start setup finished with warnings."),
                "\n".join(problems), (_("Details"), "win.setup-details"),
            ))
        stop_error = util.clean(status.get("stop_error") or "")
        if stop_error and state in text.ACTIVE:
            notices.append((
                "dialog-warning-symbolic", "warning", _("Shutdown didn't finish"),
                text.explain(stop_error) or _("The guest didn't confirm the shutdown."),
                stop_error, (_("Force Off…"), "win.force-off"),
            ))
        if cfg.get("os") == "windows11" and cfg.get("install_media"):
            if state in ("created", "stopped"):
                notices.append((
                    "dialog-information-symbolic", "accent",
                    _("Windows is installed the first time you start it"),
                    _("Setup runs offline and takes a while. Open the display to follow it."),
                    "", None,
                ))
            elif state == "running":
                notices.append((
                    "dialog-information-symbolic", "accent", _("Windows setup is running"),
                    _("Open the display to follow it."), "", None,
                ))
        key = tuple((n[2], n[3], n[5]) for n in notices)
        if key == self.notice_key:
            return
        self.notice_key = key
        self.notices.remove_all()
        for icon, kind, title, subtitle, detail, button in notices:
            row = Adw.ActionRow(title=title, subtitle=subtitle, subtitle_selectable=True)
            image = Gtk.Image(icon_name=icon, valign=Gtk.Align.CENTER)
            image.add_css_class("notice-icon")
            if kind != "accent":
                image.add_css_class(kind)
            else:
                image.add_css_class("dim-label")
            row.add_prefix(image)
            if detail and detail != subtitle:
                row.set_tooltip_text(detail)
            if button:
                label, action = button
                widget = util.button(label=label)
                if "::" in action:
                    action, target = action.split("::", 1)
                    widget.set_action_target_value(GLib.Variant.new_string(target))
                widget.set_action_name(action)
                row.add_suffix(widget)
            self.notices.append(row)
        self.notices.set_visible(bool(notices))

    def update_overview(self, cfg, status):
        if "os" not in cfg:
            self.overview.set_visible(False)
            return
        self.overview.set_visible(True)
        display = cfg["display"]
        rows = self.rows
        rows["system"].set_subtitle(text.system(cfg))
        rows["resources"].set_subtitle(_("{cores} · {memory} memory · {disk} disk").format(
            cores=text.cores(cfg["cpus"]), memory=text.gigabytes(cfg["memory_mib"]),
            disk=text.disk(cfg["disk_gib"]),
        ))
        graphics = text.graphics(display["gpu"])
        if display["gpu"] != "none":
            graphics += " · " + text.resolution(display["width"], display["height"])
        rows["graphics"].set_subtitle(graphics)
        network = _(text.NETWORK[cfg["network_mode"]])
        if status.get("ipv4") and status.get("state") == "running":
            network += " · " + status["ipv4"]
        rows["network"].set_subtitle(network)
        forwards = ", ".join(
            f"{f['proto'].upper()} {f['host']} → {f['guest']}" for f in cfg["forwards"]
        )
        rows["forwards"].set_subtitle(forwards)
        rows["forwards"].set_visible(bool(forwards))
        shares = ", ".join(s["tag"] for s in cfg["shares"])
        rows["shares"].set_subtitle(shares)
        rows["shares"].set_visible(bool(shares))
        linux = cfg["os"] == "linux" and bool(cfg.get("ssh_public_key"))
        rows["ssh"].set_subtitle("gunyahvm ssh " + cfg["name"] if linux else "")
        rows["ssh"].set_visible(linux)
