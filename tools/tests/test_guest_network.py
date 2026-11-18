# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The guest's network card is configured at every boot, not only at cloud-init's first.

Each boot runs the guest setup the host put on that boot's seed, against a
guest root that keeps its disk between boots; netplan is the only stand-in.
"""

import json
import shutil
from types import SimpleNamespace

from test_guest_desktop import guest_module, make_seed

SETUP = "/usr/local/libexec/gunyahvm-guest-setup"
RENDERED = "run/systemd/network/10-netplan-vmnet.network"


class Guest:
    """A guest root with netplan: its boot-time generator and `netplan apply`."""

    def __init__(self, root):
        self.root, self.calls = root, []
        (root / "usr/sbin").mkdir(parents=True)
        (root / "usr/sbin/netplan").write_text("")

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        self.render()
        return SimpleNamespace(returncode=0)

    def render(self):
        if list((self.root / "etc/netplan").glob("*.yaml")):
            (self.root / RENDERED).parent.mkdir(parents=True, exist_ok=True)
            (self.root / RENDERED).write_text("[Match]\n")

    def boot(self, card=None):
        """A new boot: /run starts empty, the generator renders /etc/netplan, the card is there or not."""
        shutil.rmtree(self.root / "run", ignore_errors=True)
        shutil.rmtree(self.root / "sys", ignore_errors=True)
        self.render()
        if card:
            (self.root / "sys/class/net/enp0s4").mkdir(parents=True)
            (self.root / "sys/class/net/enp0s4/address").write_text(card + "\n")


def seed_settings(tmp_path, monkeypatch):
    directory = make_seed(tmp_path, monkeypatch)
    settings = json.loads((directory / "runtime.json").read_text())
    network = json.loads((directory / "network-config").read_text())
    # The guest setup's card is the one cloud-init's network-config names.
    assert settings["mac"] == network["ethernets"]["vmnet"]["match"]["macaddress"]
    return guest_module(directory, SETUP), settings


def test_a_vm_first_started_without_networking_gets_its_network_later(tmp_path, monkeypatch):
    setup, settings = seed_settings(tmp_path, monkeypatch)
    guest = Guest(tmp_path / "guest")
    # First boot with networking off: no card, and cloud-init's first boot fails
    # before it renders any network configuration.
    guest.boot()
    setup.ensure_network(settings, guest.root, guest)
    netplan = guest.root / setup.NETPLAN
    assert netplan.stat().st_mode & 0o777 == 0o600
    text = netplan.read_text()
    for line in ("    vmnet:", f'        macaddress: "{settings["mac"]}"', "      set-name: vmnet0",
                 "      dhcp4: true", "      dhcp6: false"):
        assert line + "\n" in text
    # cloud-init's own rendering, where it exists, is read after this file.
    assert netplan.name < "50-cloud-init.yaml"
    assert guest.calls == []

    # Networking turned on: the next boot has the card, configured from the start.
    guest.boot(card=settings["mac"])
    assert (guest.root / RENDERED).exists()
    setup.ensure_network(settings, guest.root, guest)
    assert guest.calls == [] and netplan.read_text() == text


def test_a_card_nothing_configured_at_this_boot_is_configured_now(tmp_path, monkeypatch):
    # A VM first started offline by an earlier version: no configuration on disk at all.
    setup, settings = seed_settings(tmp_path, monkeypatch)
    guest = Guest(tmp_path / "guest")
    guest.boot(card=settings["mac"].upper())
    setup.ensure_network(settings, guest.root, guest)
    assert guest.calls == [["netplan", "apply"]]
    guest.boot(card=settings["mac"])
    setup.ensure_network(settings, guest.root, guest)
    assert guest.calls == [["netplan", "apply"]]


def test_a_card_cloud_init_configured_is_left_running(tmp_path, monkeypatch):
    setup, settings = seed_settings(tmp_path, monkeypatch)
    guest = Guest(tmp_path / "guest")
    (guest.root / "etc/netplan").mkdir(parents=True)
    (guest.root / "etc/netplan/50-cloud-init.yaml").write_text("network: {version: 2}\n")
    guest.boot(card=settings["mac"])
    setup.ensure_network(settings, guest.root, guest)
    assert (guest.root / setup.NETPLAN).is_file()
    assert guest.calls == []


def test_a_guest_without_netplan_is_left_alone(tmp_path, monkeypatch):
    setup, settings = seed_settings(tmp_path, monkeypatch)
    root = tmp_path / "guest"
    (root / "sys/class/net/eth0").mkdir(parents=True)
    (root / "sys/class/net/eth0/address").write_text(settings["mac"] + "\n")
    calls = []
    setup.ensure_network(settings, root, lambda argv, **kwargs: calls.append(argv))
    assert not (root / "etc").exists() and calls == []
