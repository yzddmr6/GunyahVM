# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
from gi.repository import Adw
from .. import capabilities
from . import util


class HostDialog(Adw.Window):
    def __init__(self, parent):
        super().__init__(
            transient_for=parent,
            title="宿主信息 / Host information",
            default_width=720,
            default_height=720,
        )
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        header.pack_end(
            util.button(label="加载驱动 / Load drivers", cb=self.load_drivers)
        )
        view.add_top_bar(header)
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="能力 / Capabilities")
        page.add(group)
        view.set_content(page)
        self.set_content(view)

        def show(result, error):
            if error:
                group.add(util.info_row("Error", error))
                return
            snap, rows = result
            for name, good, detail, fix in rows:
                state = "UNKNOWN" if good is None else "PASS" if good else "FAIL"
                group.add(util.info_row(name + ": " + state, detail + "\n" + fix))
            group.add(util.info_row("CPU topology", str(snap.cpus)))
            group.add(util.info_row("GPU render nodes", str(snap.render_nodes)))

        def collect():
            snapshot = capabilities.probe()
            return snapshot, capabilities.diagnostic_rows(snapshot)

        util.in_thread(collect, show)

    def load_drivers(self):
        from ..privilege import call

        def done(result, error):
            if error:
                util.error(self, "无法加载 / Cannot load", error)
            else:
                self.get_transient_for().refresh_host()
                self.close()

        util.in_thread(lambda: call("load"), done)
