# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The guest desktop is installed at every boot until it has been once.

Each boot runs the guest files the host put on that boot's seed, against a
guest root that keeps its disk between boots; apt is the only stand-in.
"""

import gzip
import importlib.machinery
import importlib.util
import json
import os
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from gunyahvm import config, seed


def make_seed(tmp_path, monkeypatch, gpu="virgl", distribution="ubuntu"):
    monkeypatch.setattr(seed, "run", lambda argv: open(
        [str(a) for a in argv][[str(a) for a in argv].index("-output") + 1], "wb").close())
    cfg = config.complete_new({
        "id": "12345678-1234-4234-8234-123456789abc", "name": "ubuntu", "uid": os.getuid(),
        "gid": os.getgid(), "os": "linux", "cpus": 2, "memory_mib": 2048, "disk_gib": 16,
        "subnet": "10.203.42.0/24", "vhost_net": False, "username": "ubuntu",
        "ssh_public_key": "ssh-ed25519 AAAA test", "guest_payload": False,
        "source_image": "Ubuntu 26.04 release-20261016",
    })
    cfg["display"]["gpu"] = gpu
    cfg["guest_distribution"] = distribution
    vm = tmp_path / "vm"
    vm.mkdir()
    seed.seed(vm, cfg, cfg["ssh_public_key"])
    return vm / "seed"


def guest_module(directory, path):
    """Load the guest program the seed carries for PATH, as the guest would run it."""
    source = directory / path.strip("/").replace("/", "_")
    loader = importlib.machinery.SourceFileLoader(path.rsplit("/", 1)[1].replace("-", "_"), str(source))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


class Apt:
    """apt-get and dpkg stand-in: a network that may be down, and a dpkg database."""

    def __init__(self, root):
        self.root, self.online, self.calls = root, True, []
        (root / "var/lib/dpkg").mkdir(parents=True, exist_ok=True)
        (root / "var/log").mkdir(parents=True, exist_ok=True)
        (root / "var/lib/dpkg/status").write_text("Package: bash\nStatus: install ok installed\n\n")

    def installed(self, name, value=True):
        status = self.root / "var/lib/dpkg/status"
        stanzas = [s for s in status.read_text().split("\n\n") if s and f"Package: {name}\n" not in s + "\n"]
        if value:
            stanzas.append(f"Package: {name}\nStatus: install ok installed")
        status.write_text("\n\n".join(stanzas) + "\n\n")

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        assert kwargs["env"]["DEBIAN_FRONTEND"] == "noninteractive"
        if argv[0] == "apt-get" and not self.online:
            return SimpleNamespace(returncode=100, stderr="", stdout=(
                "Err:1 http://ports.ubuntu.com/ubuntu-ports resolute InRelease\n"
                "E: Unable to locate package ubuntu-desktop-minimal\n"))
        if argv[:2] == ["apt-get", "-q"] and "install" in argv:
            for name in argv[argv.index("install") + 1:]:
                self.installed(name)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    def installs(self):
        return [argv[argv.index("install") + 1:] for argv in self.calls if "install" in argv]


def boot(directory, root, apt):
    """One guest boot: the readiness report the host receives, after the desktop step."""
    setup = guest_module(directory, "/usr/local/libexec/gunyahvm-guest-setup")
    control = guest_module(directory, "/usr/local/sbin/gunyahvm-control")
    settings = json.loads((directory / "runtime.json").read_text())

    def run(argv, **kwargs):
        if argv == [control.SETUP, "--desktop"]:
            result = setup.desktop(settings, root, apt)
            return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps(result) + "\n")
        return SimpleNamespace(returncode=0, stdout=json.dumps({"status": "done", "errors": []}))

    return control.setup_report(run)


def test_failed_first_install_is_installed_at_the_next_boot(tmp_path, monkeypatch, capsys):
    directory = make_seed(tmp_path, monkeypatch)
    user = json.loads((directory / "user-data").read_text().split("\n", 1)[1])
    # cloud-init would try these once per instance and never again.
    assert "packages" not in user and "package_update" not in user
    root = tmp_path / "guest"
    apt = Apt(root)
    apt.online = False
    first = boot(directory, root, apt)
    assert first["status"] == "error"
    assert first["problems"][0].startswith(
        "The desktop could not be installed; GunyahVM tries again at the next start: ")
    assert "E: Unable to locate package ubuntu-desktop-minimal" in first["problems"][0]
    assert json.loads((root / "var/lib/gunyahvm/desktop.json").read_text()) == {"delivered": []}

    apt.online = True
    assert boot(directory, root, apt) == {"status": "done", "problems": []}
    assert apt.installs()[-1] == seed.DESKTOP
    assert apt.calls[-3:-1] == [["dpkg", "--force-confdef", "--force-confold", "--configure", "-a"],
                                ["apt-get", "-q", "-o", "DPkg::Lock::Timeout=3600", "update"]]
    assert json.loads((root / "var/lib/gunyahvm/desktop.json").read_text()) == {
        "delivered": seed.DESKTOP}
    attempts = len(apt.installs())
    assert boot(directory, root, apt) == {"status": "done", "problems": []}
    assert len(apt.installs()) == attempts  # nothing is owed any more


def test_a_desktop_the_user_removed_is_not_installed_again(tmp_path, monkeypatch):
    directory = make_seed(tmp_path, monkeypatch)
    root = tmp_path / "guest"
    apt = Apt(root)
    boot(directory, root, apt)
    apt.installed("ubuntu-desktop-minimal", False)
    apt.installed("wl-clipboard", False)
    for _ in range(2):
        assert boot(directory, root, apt) == {"status": "done", "problems": []}
    assert apt.installs() == [seed.DESKTOP]


@pytest.mark.parametrize("history,installs", [
    ("installed", []),  # cloud-init of 1.4.0 or earlier installed it
    ("removed", []),  # ... and the user removed it later
    ("failed", [seed.DESKTOP]),  # its one attempt failed
])
def test_guests_from_earlier_versions(tmp_path, monkeypatch, history, installs):
    directory = make_seed(tmp_path, monkeypatch)
    root = tmp_path / "guest"
    apt = Apt(root)
    if history == "installed":
        apt.installed("ubuntu-desktop-minimal")
    if history == "removed":
        (root / "var/log/dpkg.log.3.gz").write_bytes(gzip.compress(
            b"2026-11-10 09:30:01 status installed ubuntu-desktop-minimal:arm64 1.539\n"
            b"2026-11-12 18:02:44 remove ubuntu-desktop-minimal:arm64 1.539 <none>\n"))
    (root / "var/log/dpkg.log").write_text("2026-11-18 08:00:00 status installed bash:arm64 5.2\n")
    assert boot(directory, root, apt) == {"status": "done", "problems": []}
    assert apt.installs() == installs
    assert json.loads((root / "var/lib/gunyahvm/desktop.json").read_text()) == {
        "delivered": seed.DESKTOP}


@pytest.mark.parametrize("history", ["first boot", "installed once", "delivered"])
def test_a_desktop_left_unpacked_beside_its_configuration_file_is_finished(tmp_path, monkeypatch, history):
    # The boot that installed the desktop stopped after dpkg had unpacked it, and the guest setup
    # then wrote /etc/gdm3/custom.conf for automatic login. Configuring the package makes dpkg ask
    # about that file, and the guest agent's standard input is at end of file. This is the real
    # dpkg, on a scratch database.
    if not (shutil.which("dpkg") and shutil.which("dpkg-deb")):
        pytest.skip("dpkg and dpkg-deb are needed")
    directory = make_seed(tmp_path, monkeypatch)
    setup = guest_module(directory, "/usr/local/libexec/gunyahvm-guest-setup")
    settings = json.loads((directory / "runtime.json").read_text())
    package, root = tmp_path / "package", tmp_path / "guest"
    for path in ("DEBIAN", "etc/gdm3"):
        (package / path).mkdir(parents=True)
    (package / "etc/gdm3/custom.conf").write_text("[daemon]\n")
    (package / "DEBIAN/conffiles").write_text("/etc/gdm3/custom.conf\n")
    (package / "DEBIAN/control").write_text(
        "Package: ubuntu-desktop-minimal\nVersion: 1.539\nArchitecture: all\n"
        "Maintainer: GunyahVM tests <tests@example.invalid>\nDescription: desktop\n")
    for path in (package, *package.rglob("*")):  # dpkg-deb refuses a control directory under umask 077
        path.chmod(0o755 if path.is_dir() else 0o644)
    subprocess.run(["dpkg-deb", "--root-owner-group", "--build", package, tmp_path / "desktop.deb"],
                   check=True, capture_output=True)
    for path in ("var/lib/dpkg/updates", "var/lib/dpkg/info", "var/log", "etc/gdm3"):
        (root / path).mkdir(parents=True)
    for name in ("status", "available"):
        (root / "var/lib/dpkg" / name).touch()

    def dpkg(*args, env=None):
        return subprocess.run(
            ["dpkg", f"--root={root}", "--force-not-root", "--force-bad-path",
             f"--log={root}/var/log/dpkg.log", *args],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env, check=False)

    assert dpkg("--unpack", str(tmp_path / "desktop.deb")).returncode == 0
    login = "[daemon]\nAutomaticLoginEnable = true\n"
    (root / "etc/gdm3/custom.conf").write_text(login)
    if history == "installed once":  # by 1.4.0's cloud-init; an upgrade stopped since
        with (root / "var/log/dpkg.log").open("a") as log:
            log.write("2026-11-10 09:30:01 status installed ubuntu-desktop-minimal:all 1.538\n")
    if history == "delivered":  # by this version; an upgrade stopped since
        (root / "var/lib/gunyahvm").mkdir()
        (root / "var/lib/gunyahvm/desktop.json").write_text(json.dumps({"delivered": settings["desktop"]}))

    def run(argv, **kwargs):
        if argv[0] == "dpkg":
            return dpkg(*argv[1:], env=kwargs["env"])
        if "install" in argv and setup.dpkg_states("ubuntu-desktop-minimal", root) & setup.UNFINISHED:
            return SimpleNamespace(returncode=100, stdout="", stderr=(
                "E: dpkg was interrupted, you must manually run 'dpkg --configure -a' to correct the problem.\n"))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = setup.desktop(settings, root, run)
    assert result["status"] == "installed" and "ubuntu-desktop-minimal" in result["installed"], result
    assert setup.dpkg_states("ubuntu-desktop-minimal", root) == {"installed"}
    assert (root / "etc/gdm3/custom.conf").read_text() == login


@pytest.mark.parametrize("gpu,distribution", [("none", "ubuntu"), ("virgl", "")])
def test_no_desktop_is_owed_without_a_graphics_mode_or_for_imported_images(
        tmp_path, monkeypatch, gpu, distribution):
    directory = make_seed(tmp_path, monkeypatch, gpu, distribution)
    root = tmp_path / "guest"
    apt = Apt(root)
    assert boot(directory, root, apt) == {"status": "done", "problems": []}
    assert apt.calls == [] and not (root / "var/lib/gunyahvm").exists()
