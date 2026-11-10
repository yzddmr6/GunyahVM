#!/usr/bin/python3
# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Uses the iceland-vms screenshot renderer and exercises real GTK widgets offline.
"""Drive every page of the GTK frontend and save screenshots: smoke_gui.py OUTDIR.

A synthetic ready host stands in for Gunyah so every page has realistic content;
the real probe is used once for the System Check of the machine running the test.
"""

import os
from pathlib import Path
import sys

INSTALLED = bool(os.environ.get("GUNYAHVM_TEST_INSTALLED"))
sys.path.insert(
    0,
    "/usr/lib/gunyahvm/python" if INSTALLED else str(Path(__file__).resolve().parents[2] / "src"),
)
from gunyahvm import capabilities, config, core, devices, storage, updates  # noqa: E402
from gunyahvm.ui import shots  # noqa: E402
from gunyahvm.ui.i18n import _  # noqa: E402
from gunyahvm.ui.app import App  # noqa: E402
from gunyahvm.ui.hostinfo import SystemCheck  # noqa: E402
from gunyahvm.ui.settings import Preferences  # noqa: E402
from gunyahvm.ui.wizard import CreateDialog  # noqa: E402
from gi.repository import Adw, GLib, Gtk  # noqa: E402

out = Path(sys.argv[1]).resolve()
out.mkdir(parents=True, exist_ok=True)
os.environ["XDG_CONFIG_HOME"] = str(out / "config")
os.environ["XDG_CACHE_HOME"] = str(out / "cache")
os.environ.pop("GUNYAHVM_NO_UPDATE_CHECK", None)
updates.fetch_latest = lambda: {"version": "v9.9.9", "url": updates.RELEASE_PREFIX + "v9.9.9"}

REPO = Path(__file__).resolve().parents[2]
real_probe = capabilities.probe


def ready_host():
    features = {
        "crosvm": ["gunyah", "simplefb", "gpu", "vnc", "p9", "input-modes", "balloon",
                   "vhost-net", "runtime-share", "shared-memory"],
        "renderer": ["virgl", "venus", "drm-msm"],
        "guest_kernel": ["virtio-gpu", "guest-accept", "shared-boot"],
        "guest_mesa": ["freedreno-virtio"],
        "uefi": ["windows-ecam"],
        "windows_drivers": [],
        "windows_openssh": ["offline"],
    }
    return capabilities.Snapshot(
        device_access=True,
        protected_share=False,
        firmware={"hypervisor": True, "capability_ids": {"rm": [1, 2]}},
        profile=devices.match(["xiaomi,piano"], devices.DIRECTORY if INSTALLED else REPO / "data/devices"),
        runtime={"components": {k: {"version": "1", "features": v} for k, v in features.items()}},
        render_nodes=[{"path": "/dev/dri/renderD128", "driver": "msm",
                       "model": "Adreno 830", "accessible": True}],
        vhost_net=True,
        thp="always [within_size] advise never deny",
        memory={"MemTotal": 15872, "MemAvailable": 9300},
        cpus=[{"id": i, "capacity": "", "max_khz": "", "cluster": ""} for i in range(8)],
        readings={
            "batteries": [{"name": "battery", "percent": 76, "status": "Discharging"}],
            "temperatures": [{"zone": "thermal_zone12", "type": "cpu-1-0", "trips": [],
                              "celsius": 43.1, "passive_c": 95.0, "stop_c": 105.0,
                              "stop_trip": "hot"}],
        },
    )


capabilities.probe = lambda *a, **k: ready_host()
# A supervisor is "ours" by PID; process start ticks are synthesized under qemu-user.
core.monitor_active = lambda state: state.get("supervisor_pid") == os.getpid()
store = out / "store"


def write_vm(name, osname, state, **extra):
    vm = store / "vms" / name
    vm.mkdir(parents=True, exist_ok=True)
    windows = osname == "windows11"
    cfg = config.complete_new({
        "id": f"12345678-1234-4234-8234-{abs(hash(name)) % 10**12:012d}",
        "name": name, "uid": os.getuid(), "gid": os.getgid(), "os": osname,
        "cpus": 4 if windows else 2, "memory_mib": 4608 if windows else 2048,
        "disk_gib": 64 if windows else 16, "subnet": "10.203.42.0/24",
        "vhost_net": False,
        **({"install_media": True, "windows_edition": "Professional",
            "display": config.display_defaults(True)} if windows else
           {"username": "ubuntu", "ssh_public_key": "ssh-ed25519 AAAA test",
            "source_image": "Ubuntu 26.04 release-20261016"}),
    })
    cfg.update(extra.pop("cfg", {}))
    storage.atomic_json(vm / "config.json", config.validate(cfg))
    storage.atomic_json(vm / "state.json", {"state": state, **extra})
    return cfg


def running():
    pid = os.getpid()
    return {"supervisor_pid": pid, "supervisor_ticks": core.process_ticks(pid),
            "host_boot_id": core.boot_id(), "ipv4": "10.203.42.15", "guest_ready": True}


ubuntu = write_vm("ubuntu", "linux", "running", **running(), cfg={
    "guest_payload": True,
    "display": {**config.display_defaults(), "gpu": "virgl"},
    "forwards": [{"proto": "tcp", "host": 2222, "guest": 22}],
})
write_vm("debian", "linux", "failed", error="startup failed; see state.json and supervisor.log",
         cfg={"source_image": "/srv/images/debian-13-arm64.qcow2"})
write_vm("windows", "windows11", "created")

app = App(store)
app.connect("startup", lambda *_: Gtk.IconTheme.get_for_display(
    __import__("gi.repository.Gdk", fromlist=["Gdk"]).Display.get_default()
).add_search_path(str(REPO / "packaging/usr/share/icons")))
errors, dialogs = [], []
sys.excepthook = lambda typ, value, tb: (
    errors.append(str(value)), sys.__excepthook__(typ, value, tb),
)


def snap(name, widget=None):
    return shots.save(widget or app.win, str(out / (name + ".png")))


def win():
    return app.win


def select(name):
    win().select(name, show=True)


def present(dialog):
    dialogs.append(dialog)
    dialog.present(win())


def close_dialogs():
    dialogs.clear()
    for _ in range(8):
        dialog = win().get_visible_dialog()
        if dialog is None:
            return
        dialog.force_close()


def overview():
    w = win()
    assert set(w.values) == {"ubuntu", "debian", "windows"}, w.values.keys()
    assert w.update_banners[0].get_revealed(), "update banner missing"
    assert "9.9.9" in w.update_banners[0].get_title()
    assert not w.host_banners[0].get_revealed()
    select("ubuntu")
    # Extra height in a tall or full-screen window must not go to the tile (it would stop being square).
    assert not w.page.tile.compute_expand(Gtk.Orientation.VERTICAL), "VM tile expands vertically"
    value = w.values["ubuntu"]
    assert w.page.primary_action == "shutdown", (
        w.page.primary_action, value["status"].get("state"), value["config"]["display"])
    assert w.lookup_action("shutdown").get_enabled()
    assert not w.lookup_action("start").get_enabled()


booting = {}


def guest_starting():
    # Running, but the guest has not reported ready: a clean shutdown is not possible yet.
    path = store / "vms" / "ubuntu" / "state.json"
    booting.update(core.read_json(path))
    storage.atomic_json(path, {k: v for k, v in booting.items() if k not in ("ipv4", "guest_ready")})
    win().reload()


def starting_shutdown():
    w = win()
    assert not w.lookup_action("restart").get_enabled(), "restart offered before the guest is ready"
    stops, real_stop = [], core.stop_vm
    core.stop_vm = lambda *a, **k: stops.append(a)
    try:
        w.activate_action("win.shutdown", None)
    finally:
        core.stop_vm = real_stop
    dialog = w.get_visible_dialog()
    assert isinstance(dialog, Adw.AlertDialog), dialog
    assert dialog.get_heading() == _("“{name}” Is Still Starting").format(name="ubuntu")
    assert not stops and "ubuntu" not in w.busy, "shutdown sent to a guest that cannot honour it"


def guest_ready():
    close_dialogs()
    storage.atomic_json(store / "vms" / "ubuntu" / "state.json", booting)
    win().reload()


def setup_failed():
    # The guest reported ready, but its first-start setup failed: say so and offer the details.
    path = store / "vms" / "ubuntu" / "state.json"
    storage.atomic_json(path, {**booting, "guest_setup": {
        "status": "error - done",
        "problems": ["('package_update_upgrade_install', ProcessExecutionError('dpkg returned 1'))"]}})
    win().reload()


def setup_details():
    w = win()
    assert w.lookup_action("setup-details").get_enabled()
    w.activate_action("win.setup-details", None)
    dialog = w.get_visible_dialog()
    assert isinstance(dialog, Adw.AlertDialog), dialog
    assert dialog.get_heading() == _("Setup Problems in “{name}”").format(name="ubuntu")


def windows_page():
    select("windows")
    w = win()
    assert w.page.primary_action == "start"
    assert not w.page.primary.get_sensitive(), "start allowed while another VM runs"
    assert w.page.notices.get_row_at_index(0) is not None


def failed_page():
    select("debian")
    assert win().page.primary.get_sensitive() is False  # ubuntu is running


def settings():
    w = win()
    w.open_settings()
    dialog = w.settings
    dialogs.append(dialog)
    assert dialog.get_title().startswith("debian")
    dialog.apply(lambda c: c["forwards"].append({"proto": "tcp", "host": 8080, "guest": 80}))
    dialog.apply(lambda c: c["shares"].append({"path": str(out), "tag": "shared", "ro": False}))
    stored = core.read_json(store / "vms/debian/config.json")
    assert stored["forwards"][0]["host"] == 8080 and stored["shares"][0]["tag"] == "shared"
    assert "virgl" in dialog.gpu.keys and "native" not in dialog.gpu.keys


def settings_focus():
    # Opening settings must not put the cursor in a text field (on-screen keyboard on tablets).
    focus = win().get_focus()
    assert not isinstance(focus, Gtk.Editable), f"settings opened with focus in {focus!r}"


def settings_page(index):
    def show():
        dialog = win().settings
        dialog.set_visible_page(dialog.pages[index])
    return show


def console():
    close_dialogs()
    win().open_console()
    dialogs.append(win().console)


def wizard():
    close_dialogs()
    present(CreateDialog(win()))


def wizard_ubuntu():
    dialog = dialogs[-1]
    assert "windows11" in dialog.choices, "Windows choice missing with the feature present"
    dialog.open("cloud")
    details = dialog.details
    assert details.name.get_text() == "ubuntu-2"
    assert details.create_button.get_sensitive()
    details.name.set_text("Bad Name")
    assert not details.create_button.get_sensitive()
    details.name.set_text("desktop")


def wizard_windows():
    dialog = dialogs[-1]
    dialog.nav.pop()
    dialog.open("windows11")
    details = dialog.details
    iso = out / "windows-fixture.iso"
    with iso.open("wb") as stream:
        stream.seek(0x8001)
        stream.write(b"CD001")
    details.use_file(iso)
    assert details.name.get_text() == "windows-2"
    details.password.set_text("secret")
    details.confirm.set_text("secrex")
    assert not details.create_button.get_sensitive()
    details.confirm.set_text("secret")
    assert details.create_button.get_sensitive()
    assert util_memory(details) == 4608


captured = {}


def windows_create():
    def fake_create(request):
        captured.update(
            password=os.read(request.password_fd, 64), os=request.os,
            user=request.local_user, gpu=request.gpu, memory=request.memory_mib,
        )

    core.create = fake_create
    details = dialogs[-1].details
    details.create()
    assert details.password.get_text() == "" and details.confirm.get_text() == ""
    assert "windows-2" in win().pending


def windows_created():
    assert captured == {
        "password": b"secret\n", "os": "windows11", "user": captured["user"],
        "gpu": "simplefb", "memory": 4608,
    }, captured
    assert win().pending["windows-2"].get("done")
    win().forget_pending("windows-2")
    assert "windows-2" not in win().names()


def util_memory(details):
    from gunyahvm.ui import util

    return util.combo_key(details.memory)


def system_check():
    close_dialogs()
    present(SystemCheck(win()))


def system_check_real():
    close_dialogs()
    capabilities.probe = real_probe
    present(SystemCheck(win()))


def preferences():
    close_dialogs()
    pref = Preferences(win())
    present(pref)
    assert pref.check_updates.get_active()
    pref.check_updates.set_active(False)
    assert not updates.enabled()
    assert not win().update_banners[0].get_revealed()


def confirm_delete():
    close_dialogs()
    select("debian")
    win().confirm_delete("debian")


def host_not_ready():
    w = win()
    close_dialogs()
    w.host_loaded(None, "probe failed")
    select("debian")
    assert w.host_banners[0].get_revealed()
    assert not w.page.primary.get_sensitive()


def empty():
    import shutil

    shutil.rmtree(store / "vms")
    (store / "vms").mkdir()
    win().reload()


def empty_check():
    assert win().main.get_visible_child_name() == "empty"


def about():
    win().about()


def narrow():
    close_dialogs()
    win().host_loaded(ready_host(), None)
    win().set_default_size(420, 820)
    win().split.set_show_content(False)


def finish():
    win().close()
    app.quit()


steps = [
    ("wait", lambda: app.win is not None and len(app.win.values) == 3),
    ("action", overview), ("shot", "overview-running"),
    ("action", guest_starting), ("wait", lambda: _("Starting up") in win().page.status.get_label()),
    ("action", starting_shutdown), ("pause", 1), ("shot", "shutdown-starting"),
    ("action", guest_ready), ("wait", lambda: "10.203.42.15" in win().page.status.get_label()),
    ("action", setup_failed), ("wait", lambda: win().lookup_action("setup-details").get_enabled()),
    ("pause", 1), ("shot", "setup-errors"),
    ("action", setup_details), ("pause", 1), ("shot", "setup-details"),
    ("action", guest_ready), ("wait", lambda: not win().lookup_action("setup-details").get_enabled()),
    ("action", windows_page), ("shot", "overview-windows"),
    ("action", failed_page), ("shot", "overview-failed"),
    ("action", settings), ("pause", 2), ("shot", "settings-general"),
    ("action", settings_focus),
    ("action", settings_page(1)), ("shot", "settings-display"),
    ("action", settings_page(2)), ("shot", "settings-network"),
    ("action", settings_page(3)), ("shot", "settings-sharing"),
    ("action", settings_page(4)), ("shot", "settings-advanced"),
    ("action", console), ("shot", "console"),
    ("action", wizard), ("shot", "create-choose"),
    ("action", wizard_ubuntu), ("shot", "create-ubuntu"),
    ("action", wizard_windows), ("shot", "create-windows"),
    ("action", windows_create), ("shot", "create-windows-pending"),
    ("wait", lambda: "password" in captured and win().pending.get("windows-2", {}).get("done")),
    ("action", windows_created),
    ("action", system_check), ("wait", lambda: dialogs[-1].stack.get_visible_child_name() == "result"),
    ("shot", "system-check"),
    ("action", system_check_real), ("wait", lambda: dialogs[-1].stack.get_visible_child_name() == "result"),
    ("shot", "system-check-unready"),
    ("action", preferences), ("shot", "preferences"),
    ("action", confirm_delete), ("shot", "confirm-delete"),
    ("action", host_not_ready), ("shot", "host-not-ready"),
    ("action", narrow), ("pause", 3), ("shot", "narrow-list"),
    ("action", lambda: select("ubuntu")), ("pause", 2), ("shot", "narrow-vm"),
    ("action", lambda: win().set_default_size(1200, 820)), ("pause", 3),
    ("action", empty), ("wait", lambda: win().main.get_visible_child_name() == "empty"),
    ("action", empty_check), ("shot", "empty"),
    ("action", about), ("shot", "about"),
    ("action", finish),
]
tries = 0


def step():
    global tries
    try:
        kind, item = steps[0]
        if kind == "shot":
            if not snap(item):
                tries += 1
                if tries < 8:
                    return True
                raise RuntimeError("screenshot renderer produced no node after eight frames")
        elif kind == "pause":
            tries += 1
            if tries < item:
                return True
        elif kind == "wait":
            if not item():
                tries += 1
                if tries < 60:
                    return True
                raise RuntimeError("timed out waiting")
        else:
            item()
        steps.pop(0)
        tries = 0
    except Exception as exc:
        errors.append(f"{getattr(steps[0][1], '__name__', steps[0][1])}: {exc}")
        import traceback

        traceback.print_exc()
        app.quit()
        return False
    return bool(steps)


Gtk.Settings.get_default().set_property("gtk-enable-animations", False)
GLib.timeout_add(500, step)
GLib.timeout_add_seconds(
    int(os.environ.get("GUNYAHVM_GUI_TIMEOUT", "240")),
    lambda: (errors.append("GUI timeout"), app.quit(), False)[-1],
)
app.run(["gunyahvm-smoke"])
if errors:
    raise SystemExit("; ".join(errors))
print("GTK pages PASS")
