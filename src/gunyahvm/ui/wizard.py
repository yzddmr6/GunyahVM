# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Adapted from iceland-vms (mzakamin-hub) d628b9b25d08e074b34a2f1a6113ab23767b78d7.
"""Linux image/Windows ISO creation using the shared core API."""

from pathlib import Path
import os
from gi.repository import Adw, Gtk
from .. import core, imageprobe
from . import util
from .settings import MODES


class CreateWizard(Adw.Window):
    def __init__(self, parent):
        super().__init__(
            transient_for=parent,
            modal=True,
            title="创建虚拟机 / New VM",
            default_width=650,
            default_height=720,
        )
        self.parent = parent
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        view.add_top_bar(header)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        source = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="安装来源 / Installation source")
        self.name = Adw.EntryRow(title="名称 / Name (a-z, 0-9, -)")
        self.kind = util.combo_row(
            "系统 / System",
            [
                ("cloud", "Ubuntu cloud image"),
                ("linux", "Linux ARM64 disk image"),
            ] + (
                [("windows11", "Windows 11 ARM64 ISO")]
                if parent.snapshot.features()["windows"] else []
            ),
        )
        self.path = Adw.EntryRow(title="镜像文件 / Image path")
        self.path.add_suffix(
            util.button(icon="document-open-symbolic", cb=self.choose_file)
        )
        self.mirror = util.combo_row(
            "下载源 / Download source",
            [("official", "Official"), ("tuna", "TUNA 清华"), ("ustc", "USTC 中科大")],
        )
        self.release = Adw.EntryRow(
            title="Ubuntu image build (YYYYMMDD)", text="20261016"
        )
        self.edition = Adw.EntryRow(
            title="Windows edition / index", text="Professional"
        )
        self.local_user = Adw.EntryRow(
            title="离线本地用户名 / Offline local username", text="windows"
        )
        self.local_password = Adw.PasswordEntryRow(
            title="本地账户密码 / Local account password"
        )
        for row in (
            self.name,
            self.kind,
            self.path,
            self.mirror,
            self.release,
            self.edition,
            self.local_user,
            self.local_password,
        ):
            group.add(row)
        source.add(group)
        self.windows_note = Adw.PreferencesGroup(
            description="使用本地账户离线安装；SSH 控制账户独立使用密钥。 / Install offline with a local account; the separate SSH control account uses keys."
        )
        source.add(self.windows_note)
        self.stack.add_named(source, "source")
        resources = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title="资源与显示 / Resources and display")
        self.cpu = util.spin_row("CPU", None, 1, 4, 1, 2)
        self.memory = util.spin_row("内存 / Memory (MiB)", None, 512, 5120, 256, 2048)
        self.disk = util.spin_row("磁盘 / Disk (GiB)", None, 8, 2048, 8, 16)
        self.gpu = util.combo_row("图形 / Graphics", [("none", MODES["none"])])
        self.payload = Adw.SwitchRow(
            title="使用配套来宾内核 / Use runtime guest kernel",
            subtitle="Required for 3D modes",
        )
        self.vhost = Adw.SwitchRow(title="vhost-net")
        self.vhost.set_sensitive(parent.snapshot.features()["vhost_net"])
        for row in (
            self.cpu,
            self.memory,
            self.disk,
            self.gpu,
            self.payload,
            self.vhost,
        ):
            group.add(row)
        resources.add(group)
        self.stack.add_named(resources, "resources")
        self.back = util.button(
            label="上一步 / Back",
            cb=lambda: self.stack.set_visible_child_name("source"),
        )
        self.next = util.button(
            label="下一步 / Next", css=["suggested-action"], cb=self.next_page
        )
        self.create_button = util.button(
            label="创建 / Create", css=["suggested-action"], cb=self.create
        )
        self.create_button.set_visible(False)
        header.pack_start(self.back)
        header.pack_end(self.next)
        header.pack_end(self.create_button)
        self.stack.connect("notify::visible-child-name", self.page_changed)
        self.kind.connect("notify::selected", self.kind_changed)
        view.set_content(self.stack)
        self.set_content(view)
        self.kind_changed()
        self.page_changed()

    def choose_file(self):
        chooser = Gtk.FileChooserNative(
            title="选择镜像 / Select image",
            transient_for=self,
            action=Gtk.FileChooserAction.OPEN,
        )

        def selected(dialog, response):
            if response == Gtk.ResponseType.ACCEPT:
                self.path.set_text(dialog.get_file().get_path())
            dialog.destroy()

        chooser.connect("response", selected)
        chooser.show()

    def kind_changed(self, *_):
        kind = util.combo_key(self.kind)
        self.path.set_visible(kind != "cloud")
        self.mirror.set_visible(kind == "cloud")
        self.release.set_visible(kind == "cloud")
        self.edition.set_visible(kind == "windows11")
        self.local_user.set_visible(kind == "windows11")
        self.local_password.set_visible(kind == "windows11")
        self.windows_note.set_visible(kind == "windows11")

    def page_changed(self, *_):
        source = self.stack.get_visible_child_name() == "source"
        self.back.set_visible(not source)
        self.next.set_visible(source)
        self.create_button.set_visible(not source)

    def next_page(self):
        try:
            core.vm_name(self.name.get_text())
            kind = util.combo_key(self.kind)
            if kind != "cloud":
                path = Path(self.path.get_text()).expanduser()
                if not path.is_file():
                    raise ValueError("Select an existing image file")
                fmt = imageprobe.image_format(path)
                if kind == "windows11" and fmt != "iso":
                    raise ValueError("Windows requires an ISO")
                if kind == "linux" and fmt not in ("raw", "qcow2"):
                    raise ValueError("Linux requires a raw or qcow2 disk")
            windows = kind == "windows11"
            if windows and (not self.local_user.get_text().strip() or not self.local_password.get_text()):
                raise ValueError("填写离线本地账户和密码 / Enter a local account and password")
            if windows and len(self.local_password.get_text().encode()) > 256:
                raise ValueError("Password is too long")
            self.memory.set_value(4608 if windows else 2048)
            self.disk.set_value(64 if windows else 16)
            modes = self.parent.snapshot.modes(
                guest_os="windows11" if windows else "linux"
            )
            self.gpu.keys = modes
            self.gpu.set_model(Gtk.StringList.new([MODES[m] for m in modes]))
            self.gpu.set_selected(modes.index("virgl") if "virgl" in modes else 0)
            self.payload.set_active(
                not windows
                and "guest_kernel" in self.parent.snapshot.runtime.get("components", {})
            )
            self.payload.set_visible(not windows)
            self.vhost.set_sensitive(
                not windows and self.parent.snapshot.features()["vhost_net"]
            )
            self.create_button.set_sensitive(bool(modes))
            self.stack.set_visible_child_name("resources")
        except (ValueError, OSError) as exc:
            util.error(self, "无法继续 / Cannot continue", str(exc))

    def create(self):
        kind = util.combo_key(self.kind)
        request = core.CreateRequest(
            store=self.parent.store,
            name=self.name.get_text(),
            os="windows11" if kind == "windows11" else "linux",
            image=self.path.get_text() if kind == "linux" else None,
            iso=Path(self.path.get_text()) if kind == "windows11" else None,
            edition=self.edition.get_text() or None,
            local_user=self.local_user.get_text().strip() if kind == "windows11" else None,
            release=self.release.get_text(),
            mirror=util.combo_key(self.mirror),
            cpus=int(self.cpu.get_value()),
            memory_mib=int(self.memory.get_value()),
            disk_gib=int(self.disk.get_value()),
            gpu=util.combo_key(self.gpu),
            guest_payload=self.payload.get_active() and kind != "windows11",
            vhost_net=self.vhost.get_active() and kind != "windows11",
        )
        self.create_button.set_sensitive(False)
        self.set_deletable(False)
        app = self.parent.get_application()
        app.hold()

        def done(result, error):
            app.release()
            self.set_deletable(True)
            self.create_button.set_sensitive(True)
            if error:
                util.error(self, "创建失败 / Creation failed", error)
            else:
                self.parent.reload()
                self.close()

        password = self.local_password.get_text() if kind == "windows11" else None
        self.local_password.set_text("")

        def create_vm():
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

        util.in_thread(create_vm, done)
