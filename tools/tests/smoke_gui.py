#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Uses the iceland-vms screenshot renderer and exercises real GTK widgets offline.
import os
from pathlib import Path
import sys

sys.path.insert(
    0,
    "/usr/lib/gunyahvm/python"
    if os.environ.get("GUNYAHVM_TEST_INSTALLED")
    else str(Path(__file__).resolve().parents[2] / "src"),
)
from gunyahvm import config, storage
from gunyahvm.ui.app import App
from gunyahvm.ui.wizard import CreateWizard
from gunyahvm.ui.hostinfo import HostDialog
from gunyahvm.ui.settings import Settings
from gunyahvm.ui import shots
from gi.repository import GLib

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
store = out / "store"
vm = store / "vms/linux"
vm.mkdir(parents=True, exist_ok=True)
cfg = config.complete_new(
    {
        "id": "12345678-1234-4234-8234-123456789abc",
        "name": "linux",
        "uid": os.getuid(),
        "gid": os.getgid(),
        "os": "linux",
        "cpus": 2,
        "memory_mib": 1024,
        "disk_gib": 8,
        "subnet": "10.203.42.0/24",
        "vhost_net": False,
        "username": "ubuntu",
    }
)
storage.atomic_json(vm / "config.json", cfg)
storage.atomic_json(vm / "state.json", {"state": "created"})
app = App(store)
errors, dialogs = [], []
# GTK signal exceptions normally only print: make them fail this smoke run.
sys.excepthook = lambda typ, value, tb: (
    errors.append(str(value)),
    sys.__excepthook__(typ, value, tb),
)


def snap(widget, name):
    # The upstream renderer can be temporarily empty until the first GTK frame.
    # A screenshot step is retried before moving to another page.
    return shots.save(widget, str(out / (name + ".png")))


def overview():
    assert app.win.rows, "VM collection failed"
    app.win.listbox.select_row(app.win.listbox.get_row_at_index(0))
    assert app.win.settings.collect()["id"] == cfg["id"]


def settings():
    panel = app.win.settings
    panel.add_share({"path": str(out), "tag": "shared", "ro": False})
    panel.add_forward({"proto": "tcp", "host": 2222, "guest": 22})
    value = panel.collect()
    assert (
        value["shares"][0]["tag"] == "shared" and value["forwards"][0]["host"] == 2222
    )


def console():
    app.win.tabs.set_visible_child_name("console")


def wizard():
    w = CreateWizard(app.win)
    dialogs.append(w)
    w.present()


def resources():
    w = dialogs[-1]
    w.name.set_text("new-linux")
    w.next_page()
    assert w.stack.get_visible_child_name() == "resources"


def windows_resources():
    w = dialogs[-1]
    iso = out / "windows-fixture.iso"
    with iso.open("wb") as stream:
        stream.seek(0x8001)
        stream.write(b"CD001")
    w.stack.set_visible_child_name("source")
    w.kind.set_selected(w.kind.keys.index("windows11"))
    w.path.set_text(str(iso))
    w.name.set_text("windows-fixture")
    w.next_page()
    assert w.stack.get_visible_child_name() == "resources"
    assert int(w.memory.get_value()) == 4608 and int(w.disk.get_value()) == 64
    assert w.gpu.keys == app.win.snapshot.modes(guest_os="windows11")
    windows = config.complete_new(
        {
            **cfg,
            "os": "windows11",
            "memory_mib": 4608,
            "disk_gib": 64,
            "display": config.display_defaults(True),
            "install_media": True,
        }
    )
    panel = Settings(windows, app.win.snapshot)
    assert not panel.share_group.get_sensitive() and not panel.vhost.get_sensitive()
    if panel.gpu.keys:
        assert panel.collect()["display"]["gpu"] == "simplefb"
    else:
        try:
            panel.collect()
        except ValueError:
            pass
        else:
            raise AssertionError("missing Windows runtime was accepted")


def host():
    w = dialogs.pop()
    w.close()
    h = HostDialog(app.win)
    dialogs.append(h)
    h.present()


def finish():
    dialogs[-1].close()
    app.win.close()
    app.quit()


# Action/shot separation and bounded retries follow iceland-vms ui/shots.py.
steps = [
    ("action", overview),
    ("shot", lambda: snap(app.win, "overview")),
    ("action", settings),
    ("shot", lambda: snap(app.win, "settings")),
    ("action", console),
    ("shot", lambda: snap(app.win, "console")),
    ("action", wizard),
    ("shot", lambda: snap(dialogs[-1], "wizard-source")),
    ("action", resources),
    ("shot", lambda: snap(dialogs[-1], "wizard-resources")),
    ("action", windows_resources),
    ("shot", lambda: snap(dialogs[-1], "wizard-windows")),
    ("action", host),
    ("shot", lambda: snap(dialogs[-1], "host")),
    ("action", finish),
]
tries = 0


def step():
    global tries
    if app.win is None or not app.win.rows:
        return True
    try:
        kind, fn = steps[0]
        if kind == "shot" and not fn():
            tries += 1
            if tries < 8:
                return True
            print("Existing screenshot renderer produced no node after eight frames")
        elif kind == "action":
            fn()
        steps.pop(0)
        tries = 0
    except Exception as exc:
        errors.append(str(exc))
        import traceback

        traceback.print_exc()
        app.quit()
        return False
    return bool(steps)


from gi.repository import Gtk

Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
GLib.timeout_add(700, step)
GLib.timeout_add_seconds(
    int(os.environ.get("GUNYAHVM_GUI_TIMEOUT", "120")),
    lambda: (errors.append("GUI timeout"), app.quit(), False)[-1],
)
app.run(["gunyahvm-smoke"])
if errors:
    raise SystemExit("; ".join(errors))
print("GTK pages PASS")
