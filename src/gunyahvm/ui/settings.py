# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""Resource, display, shared-folder and network controls backed by the core model."""

from copy import deepcopy
from gi.repository import Adw, Gtk
from .. import config
from . import util

MODES = {
    "none": "无显示 / Console",
    "2d": "2D",
    "simplefb": "软件显示 / Software",
    "virgl": "OpenGL (virgl)",
    "venus": "OpenGL + Vulkan (Venus)",
    "native": "Adreno native",
}


class Settings(Adw.PreferencesPage):
    def __init__(self, cfg, snapshot):
        super().__init__()
        self.cfg, self.snapshot = deepcopy(cfg), snapshot
        resources = Adw.PreferencesGroup(title="资源 / Resources")
        self.cpu = util.spin_row(
            "CPU",
            None,
            1,
            max(1, min(len(snapshot.cpus), snapshot.profile["resources"]["max_cpus"])),
            1,
            cfg["cpus"],
        )
        self.memory = util.spin_row(
            "内存 / Memory (MiB)",
            None,
            4608 if cfg["os"] == "windows11" else 512,
            snapshot.profile["resources"]["max_memory_mib"],
            256,
            cfg["memory_mib"],
        )
        resources.add(self.cpu)
        resources.add(self.memory)
        self.memmode = util.combo_row(
            "内存模式 / Memory",
            [("protected", "Protected")]
            + (
                [("shared", "Shared — host can access guest RAM")]
                if snapshot.features()["shared"] and cfg["os"] != "windows11"
                else []
            ),
        )
        util.combo_set(self.memmode, cfg["memory_mode"])
        resources.add(self.memmode)
        self.add(resources)
        display = Adw.PreferencesGroup(title="显示 / Display")
        modes = snapshot.modes(cfg["memory_mode"], cfg["os"])
        self.gpu = util.combo_row("图形 / Graphics", [(m, MODES[m]) for m in modes])
        util.combo_set(self.gpu, cfg["display"]["gpu"])
        display.add(self.gpu)
        self.payload = Adw.SwitchRow(
            title="使用配套来宾内核 / Runtime guest kernel", active=cfg["guest_payload"]
        )
        self.payload.set_sensitive(
            cfg["os"] == "linux"
            and "guest_kernel" in snapshot.runtime.get("components", {})
        )
        display.add(self.payload)
        self.memmode.connect("notify::selected", self.memory_changed)
        self.width = util.spin_row(
            "宽 / Width", None, 640, 3840, 16, cfg["display"]["width"]
        )
        self.height = util.spin_row(
            "高 / Height", None, 480, 3000, 16, cfg["display"]["height"]
        )
        self.input = util.combo_row(
            "触控 / Touch input",
            [("tablet", "Tablet"), ("touchpad", "Touchpad"), ("touch", "Multi-touch")],
        )
        util.combo_set(self.input, cfg["display"]["input_mode"])
        self.input.set_sensitive(snapshot.features()["input_modes"])
        self.fullscreen = Adw.SwitchRow(
            title="全屏 / Fullscreen", active=cfg["display"]["fullscreen"]
        )
        self.vnc = Adw.SwitchRow(
            title="VNC（仅本机 / loopback）", active=cfg["display"]["vnc"]
        )
        self.vnc.set_sensitive(snapshot.features()["vnc"] and cfg["os"] != "windows11")
        self.vnc_port = util.spin_row(
            "VNC port", None, 1024, 65535, 1, cfg["display"]["vnc_port"]
        )
        for row in (
            self.width,
            self.height,
            self.input,
            self.fullscreen,
            self.vnc,
            self.vnc_port,
        ):
            display.add(row)
        self.add(display)
        network = Adw.PreferencesGroup(title="网络 / Network")
        self.net = util.combo_row(
            "连接 / Connection", [("nat", "Shared NAT"), ("off", "Disconnected")]
        )
        util.combo_set(self.net, cfg["network_mode"])
        self.vhost = Adw.SwitchRow(title="vhost-net", active=cfg["vhost_net"])
        self.vhost.set_sensitive(
            cfg["os"] == "linux" and snapshot.features()["vhost_net"]
        )
        network.add(self.net)
        self.subnet = Adw.EntryRow(
            title="子网 / Private IPv4 subnet (/24)", text=cfg["subnet"]
        )
        self.subnet.set_sensitive(cfg["os"] == "linux")
        network.add(self.subnet)
        network.add(self.vhost)
        self.forward_rows = []
        self.forward_group = Adw.PreferencesGroup(
            title="本机端口转发 / Loopback forwards"
        )
        self.forward_group.set_header_suffix(
            util.button(
                icon="list-add-symbolic",
                cb=lambda: self.add_forward(
                    {"proto": "tcp", "host": 2222, "guest": 22}
                ),
            )
        )
        for f in cfg["forwards"]:
            self.add_forward(f)
        self.add(network)
        self.add(self.forward_group)
        self.share_rows = []
        self.share_group = Adw.PreferencesGroup(
            title="共享文件夹 / Shared folders",
            description="9p shares use your host permissions. Guest mount: mount -t 9p -o trans=virtio TAG DIRECTORY",
        )
        self.share_group.set_header_suffix(
            util.button(
                icon="list-add-symbolic",
                cb=lambda: self.add_share(
                    {"path": "", "tag": f"share{len(self.share_rows)}", "ro": False}
                ),
            )
        )
        for share in cfg["shares"]:
            self.add_share(share)
        self.share_group.set_sensitive(
            cfg["os"] == "linux" and snapshot.features()["shares"]
        )
        self.add(self.share_group)

    def memory_changed(self, *_):
        selected = util.combo_key(self.gpu) if self.gpu.keys else None
        modes = self.snapshot.modes(util.combo_key(self.memmode), self.cfg["os"])
        self.gpu.keys = modes
        self.gpu.set_model(Gtk.StringList.new([MODES[m] for m in modes]))
        self.gpu.set_selected(modes.index(selected) if selected in modes else 0)

    def add_share(self, value):
        group = Adw.ExpanderRow(title=value["tag"])
        path = Adw.EntryRow(title="宿主文件夹 / Host folder", text=value["path"])
        tag = Adw.EntryRow(title="挂载标记 / Mount tag", text=value["tag"])
        ro = Adw.SwitchRow(
            title="只读 / Read only",
            subtitle="Unavailable in the installed 9p backend",
            active=value["ro"],
        )
        ro.set_sensitive(False)
        for row in (path, tag, ro):
            group.add_row(row)
        entry = (group, path, tag, ro)

        def remove():
            self.share_group.remove(group)
            self.share_rows.remove(entry)

        group.add_suffix(util.button(icon="edit-delete-symbolic", cb=remove))
        self.share_rows.append(entry)
        self.share_group.add(group)
        group.set_expanded(True)

    def add_forward(self, value):
        row = Adw.ExpanderRow(title=f"{value['host']} → {value['guest']}")
        proto = util.combo_row("Protocol", [("tcp", "TCP"), ("udp", "UDP")])
        util.combo_set(proto, value["proto"])
        host = util.spin_row("Host port", None, 1024, 65535, 1, value["host"])
        guest = util.spin_row("Guest port", None, 1, 65535, 1, value["guest"])
        for control in (proto, host, guest):
            row.add_row(control)
        entry = (row, proto, host, guest)

        def remove():
            self.forward_group.remove(row)
            self.forward_rows.remove(entry)

        row.add_suffix(util.button(icon="edit-delete-symbolic", cb=remove))
        self.forward_rows.append(entry)
        self.forward_group.add(row)
        row.set_expanded(True)

    def collect(self):
        if not self.gpu.keys:
            raise ValueError("No compatible display runtime is installed")
        cfg = deepcopy(self.cfg)
        cfg.update(
            cpus=int(self.cpu.get_value()),
            memory_mib=int(self.memory.get_value()),
            memory_mode=util.combo_key(self.memmode),
            network_mode=util.combo_key(self.net),
            vhost_net=self.vhost.get_active(),
            guest_payload=self.payload.get_active(),
        )
        cfg["subnet"] = self.subnet.get_text()
        cfg["display"].update(
            gpu=util.combo_key(self.gpu),
            width=int(self.width.get_value()),
            height=int(self.height.get_value()),
            input_mode=util.combo_key(self.input),
            fullscreen=self.fullscreen.get_active(),
            vnc=self.vnc.get_active(),
            vnc_port=int(self.vnc_port.get_value()),
        )
        cfg["shares"] = [
            {"path": p.get_text(), "tag": t.get_text(), "ro": ro.get_active()}
            for _, p, t, ro in self.share_rows
        ]
        cfg["forwards"] = [
            {
                "proto": util.combo_key(p),
                "host": int(h.get_value()),
                "guest": int(g.get_value()),
            }
            for _, p, h, g in self.forward_rows
        ]
        return config.validate(cfg)
