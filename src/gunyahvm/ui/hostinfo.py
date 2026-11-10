# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""System Check: can this device run virtual machines, and what to do if not."""

from gi.repository import Adw, Gtk, Pango

from .. import capabilities
from . import text, util
from .i18n import _, ngettext

# memlock is raised by the authorized launcher at start; a low value in the
# desktop session is expected and not a problem for the user.
INFORMATIONAL = ("memlock",)


class SystemCheck(Adw.Dialog):
    def __init__(self, window):
        super().__init__(title=_("System Check"), content_width=620, content_height=780)
        self.window = window
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        loading = Adw.StatusPage(title=_("Checking This Device…"))
        loading.set_paintable(Adw.SpinnerPaintable(widget=loading))
        self.stack.add_named(loading, "loading")
        self.page = Adw.PreferencesPage()
        self.stack.add_named(self.page, "result")
        view.set_content(self.stack)
        self.set_child(view)
        self.check()

    def check(self):
        self.stack.set_visible_child_name("loading")

        def collect():
            snapshot = capabilities.probe()
            return snapshot, capabilities.diagnostic_rows(snapshot)

        util.in_thread(collect, self.show)

    def show(self, result, error):
        page = Adw.PreferencesPage()
        self.stack.remove(self.page)
        self.page = page
        self.stack.add_named(page, "result")
        if error:
            page.add(self.summary(False, _("The check couldn't finish."), None))
            group = Adw.PreferencesGroup()
            group.add(self.raw_row(_("Details"), error))
            page.add(group)
            self.stack.set_visible_child_name("result")
            return False
        snapshot, rows = result
        self.window.host_loaded(snapshot, None)
        problems = [
            r for r in rows if r[1] is False and r[0] not in INFORMATIONAL
        ]
        drivers = any(r[0] in ("modules", "device") and r[1] is False for r in rows)
        if problems:
            description = ngettext(
                "{count} item needs attention.", "{count} items need attention.",
                len(problems),
            ).format(count=len(problems))
        else:
            description = _("Everything needed to run virtual machines is in place.")
        page.add(self.summary(not problems, description, drivers))

        group = Adw.PreferencesGroup(title=_("Checks"))
        for name, good, detail, fix in sorted(rows, key=lambda r: r[1] is not False):
            group.add(self.check_row(name, good, detail, fix))
        page.add(group)
        page.add(self.device_group(snapshot))
        self.stack.set_visible_child_name("result")
        return False

    def summary(self, ready, description, drivers):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_bottom=6)
        icon = Gtk.Image(
            icon_name="object-select-symbolic" if ready else "dialog-warning-symbolic",
            pixel_size=48, halign=Gtk.Align.CENTER,
        )
        icon.add_css_class("check-icon")
        icon.add_css_class("good" if ready else "bad")
        box.append(icon)
        title = Gtk.Label(
            label=_("Ready for Virtual Machines") if ready else _("Not Ready Yet"),
            wrap=True, justify=Gtk.Justification.CENTER,
        )
        title.add_css_class("title-2")
        box.append(title)
        label = Gtk.Label(label=description, wrap=True, justify=Gtk.Justification.CENTER)
        label.add_css_class("dim-label")
        box.append(label)
        if drivers:
            load = util.button(label=_("_Load Drivers"), css=("pill", "suggested-action"))
            load.set_halign(Gtk.Align.CENTER)
            load.set_margin_top(6)
            load.connect("clicked", lambda *_: self.load_drivers(load))
            box.append(load)
        group = Adw.PreferencesGroup()
        group.add(box)
        return group

    def check_row(self, name, good, detail, fix):
        row = Adw.ExpanderRow(
            title=text.check_title(name), subtitle=text.check_summary(name, good, detail)
        )
        if good:
            icon, kind = "object-select-symbolic", "good"
        elif good is None or name in INFORMATIONAL:
            icon, kind = "dialog-information-symbolic", "unknown"
        else:
            icon, kind = "dialog-error-symbolic", "bad"
        image = Gtk.Image(icon_name=icon)
        image.add_css_class("check-icon")
        image.add_css_class(kind)
        row.add_prefix(image)
        row.add_row(self.raw_row(_("Details"), detail))
        if not good:
            row.add_row(self.raw_row(_("How to Fix"), fix))
        return row

    @staticmethod
    def raw_row(title, value):
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4,
            margin_top=10, margin_bottom=10, margin_start=12, margin_end=12,
        )
        heading = Gtk.Label(label=title, xalign=0)
        heading.add_css_class("caption-heading")
        heading.add_css_class("dim-label")
        body = Gtk.Label(label=value, xalign=0, wrap=True, selectable=True)
        body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        body.add_css_class("monospace")
        body.add_css_class("caption")
        box.append(heading)
        box.append(body)
        return Gtk.ListBoxRow(child=box, activatable=False)

    def device_group(self, snapshot):
        group = Adw.PreferencesGroup(title=_("This Device"))
        rows = [(_("Model"), snapshot.profile["name"])]
        if snapshot.cpus:
            rows.append((_("Processors"), text.cores(len(snapshot.cpus))))
        if snapshot.memory.get("MemTotal"):
            rows.append((_("Memory"), text.gigabytes(snapshot.memory["MemTotal"])))
        gpus = [n.get("model") for n in snapshot.render_nodes if n.get("model")]
        if gpus:
            rows.append((_("GPU"), ", ".join(gpus)))
        modes = [m for m in snapshot.modes() if m != "none"]
        rows.append((
            _("Graphics Modes"),
            ", ".join(text.graphics(m) for m in modes) or _("None available"),
        ))
        readings = snapshot.readings
        if readings["batteries"]:
            rows.append((
                _("Battery"),
                ", ".join(f"{b['percent']}%" for b in readings["batteries"]),
            ))
        temperatures = [t for t in readings["temperatures"] if "celsius" in t]
        if temperatures:
            hottest = max(temperatures, key=lambda t: t["celsius"] - t["passive_c"])
            rows.append((
                _("Temperature"),
                _("{now} °C, slows down at {limit} °C").format(
                    now=round(hottest["celsius"]), limit=round(hottest["passive_c"])
                ),
            ))
        for title, value in rows:
            row = Adw.ActionRow(title=title, subtitle=value, subtitle_selectable=True)
            row.add_css_class("property")
            group.add(row)
        return group

    def load_drivers(self, button):
        from ..privilege import call

        button.set_sensitive(False)

        def done(_result, error):
            button.set_sensitive(True)
            if error:
                util.error(self, _("Couldn't Load the Drivers"), error)
            else:
                self.window.refresh_host()
                self.check()
            return False

        util.in_thread(lambda: call("load"), done)
