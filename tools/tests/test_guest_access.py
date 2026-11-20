# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""The host's SSH key and GunyahVM's guest services follow at every boot, not only the first.

cloud-init applies both only at an instance's first boot. The host reads its
identity's key at every start and the guest setup applies the seed at every
boot, against a guest root that keeps its disk between boots.
"""

import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from gunyahvm import config, core, seed
from test_guest_desktop import guest_module, make_seed

SETUP = "/usr/local/libexec/gunyahvm-guest-setup"


def keygen(path, comment):
    path.unlink(missing_ok=True)
    path.with_name(path.name + ".pub").unlink(missing_ok=True)
    subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", comment, "-f", str(path)],
                   check=True)
    return path.with_name(path.name + ".pub").read_text().strip()


def guest_root(tmp_path, keys):
    root = tmp_path / "guest"
    (root / "etc").mkdir(parents=True)
    (root / "etc/passwd").write_text(
        "root:x:0:0:root:/root:/bin/bash\nubuntu:x:1000:1000::/srv/ubuntu:/bin/bash\n")
    (root / "srv/ubuntu/.ssh").mkdir(parents=True)
    (root / "srv/ubuntu/.ssh/authorized_keys").write_text("".join(k + "\n" for k in keys))
    return root


def test_a_replaced_host_key_is_followed_at_the_next_start(tmp_path):
    vm = tmp_path / "vm"
    vm.mkdir()
    identity = tmp_path / "id_ed25519"
    old = keygen(identity, "old")
    cfg = config.complete_new({
        "id": "12345678-1234-4234-8234-123456789abc", "name": "ubuntu", "uid": os.getuid(),
        "gid": os.getgid(), "os": "linux", "cpus": 2, "memory_mib": 2048, "disk_gib": 16,
        "subnet": "10.203.42.0/24", "vhost_net": False, "username": "ubuntu",
        "ssh_identity": str(identity), "ssh_public_key": old, "guest_payload": False,
        "source_image": "Ubuntu 26.04 release-20261016",
    })
    (vm / "config.json").write_text(json.dumps(cfg))
    # The same key: nothing changes.
    assert core.follow_control_key(vm, cfg) is cfg
    new = keygen(identity, "new")
    # The start calls it while it holds the VM's operation lock.
    from gunyahvm import storage
    with storage.operation_lock(vm):
        followed = core.follow_control_key(vm, cfg)
    stored = config.validate(json.loads((vm / "config.json").read_text()))
    for c in (followed, stored):
        assert c["ssh_public_key"] == new and c["ssh_retired_keys"] == [old]
    # Back to a retired key: it is current again and the other one retired.
    identity.with_name(identity.name + ".pub").write_text(old + "\n")
    again = core.follow_control_key(vm, stored)
    assert again["ssh_public_key"] == old and again["ssh_retired_keys"] == [new]
    # A missing or unreadable key leaves the VM's key alone.
    identity.with_name(identity.name + ".pub").unlink()
    assert core.follow_control_key(vm, again) is again
    with pytest.raises(ValueError, match="retired SSH keys"):
        config.validate(dict(stored, ssh_retired_keys=["ssh-ed25519 AAAA a\nb"]))


def test_the_guest_authorizes_the_current_key_and_drops_the_replaced_one(tmp_path, monkeypatch):
    directory = make_seed(tmp_path, monkeypatch)
    module = guest_module(directory, SETUP)
    old, new = "ssh-ed25519 AAAAC3NzaOLD old@host", "ssh-ed25519 AAAAC3NzaNEW new@host"
    mine = 'no-pty ssh-ed25519 AAAAC3NzaUSER user@laptop'
    # cloud-init wrote the old key at the first boot; the user added one of their own.
    root = guest_root(tmp_path, [old, mine])
    path = root / "srv/ubuntu/.ssh/authorized_keys"
    settings = dict(json.loads((directory / "runtime.json").read_text()),
                    ssh_key=new, retired_ssh_keys=[old])
    module.authorize_control_key(settings, root)
    assert path.read_text() == mine + "\n" + new + "\n"
    assert path.stat().st_mode & 0o777 == 0o600
    # Every later boot finds it done; the same key under another comment counts as present.
    module.authorize_control_key(dict(settings, ssh_key="ssh-ed25519 AAAAC3NzaNEW renamed"), root)
    assert path.read_text() == mine + "\n" + new + "\n"
    # A guest without the account yet (cloud-init creates it at the first boot) is left alone.
    module.authorize_control_key(dict(settings, username="nobody"), root)


def test_the_seed_carries_the_current_key_its_retired_keys_and_the_services(tmp_path, monkeypatch):
    directory = make_seed(tmp_path, monkeypatch)
    settings = json.loads((directory / "runtime.json").read_text())
    user = json.loads((directory / "user-data").read_text().split("\n", 1)[1])
    assert settings["ssh_key"] == user["users"][0]["ssh_authorized_keys"][0]
    assert settings["retired_ssh_keys"] == []
    assert settings["units"] == seed.UNITS
    assert ["systemctl", "enable", *seed.UNITS] in user["runcmd"]
    # Every service the seed names is a GunyahVM file the seed also installs at every boot.
    files = {m["path"] for m in json.loads((directory / "guest-files.json").read_text())}
    assert {"/etc/systemd/system/" + unit for unit in seed.UNITS} <= files


def test_a_service_a_later_version_adds_is_enabled_in_an_older_vm(tmp_path, monkeypatch):
    directory = make_seed(tmp_path, monkeypatch)
    module = guest_module(directory, SETUP)
    enabled = {"gunyahvm-guest-setup.service", "gunyahvm-control.service"}
    calls = []

    def systemctl(argv, **kwargs):
        calls.append(argv)
        if argv[:3] == ["systemctl", "--quiet", "is-enabled"]:
            return SimpleNamespace(returncode=0 if argv[3] in enabled else 1)
        if argv[1] == "enable":
            enabled.add(argv[2])
        return SimpleNamespace(returncode=0)

    settings = json.loads((directory / "runtime.json").read_text())
    # A VM whose first boot enabled this version's services: nothing to do.
    module.enable_units(settings, systemctl)
    assert [c[1] for c in calls] == ["--quiet", "--quiet"]
    # The next version brings a new service: enabled, and started in this boot.
    calls.clear()
    module.enable_units(dict(settings, units=settings["units"] + ["gunyahvm-new.service"]), systemctl)
    assert calls[-2:] == [["systemctl", "enable", "gunyahvm-new.service"],
                          ["systemctl", "start", "--no-block", "gunyahvm-new.service"]]
    # Only GunyahVM's own services are touched.
    with pytest.raises(ValueError, match="unit"):
        module.enable_units({"units": ["ssh.service"]}, systemctl)
