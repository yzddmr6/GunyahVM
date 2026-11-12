# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
import json
import os


def test_cloud_seed_names_the_machine_for_flash_kernel(tmp_path, monkeypatch):
    from gunyahvm import config, seed

    def iso(argv):  # no ISO tool needed to read the user-data
        out = [str(a) for a in argv][[str(a) for a in argv].index("-output") + 1]
        open(out, "wb").close()

    monkeypatch.setattr(seed, "run", iso)
    cfg = config.complete_new({
        "id": "12345678-1234-4234-8234-123456789abc", "name": "ubuntu", "uid": os.getuid(),
        "gid": os.getgid(), "os": "linux", "cpus": 2, "memory_mib": 2048, "disk_gib": 16,
        "subnet": "10.203.42.0/24", "vhost_net": False, "username": "ubuntu",
        "ssh_public_key": "ssh-ed25519 AAAA test", "source_image": "Ubuntu 26.04 release-20261016",
        "guest_payload": False,
    })
    seed.seed(tmp_path, cfg, cfg["ssh_public_key"])
    network = json.loads((tmp_path / "seed/network-config").read_text())
    nic, = network["ethernets"].values()
    assert nic["match"] == {"macaddress": cfg["mac"]}
    assert nic["set-name"] == "vmnet0" and nic["dhcp4"] is True
    text = (tmp_path / "seed/user-data").read_text()
    assert text.startswith("#cloud-config\n")
    files = {f["path"]: f for f in json.loads(text.split("\n", 1)[1])["write_files"]}
    # flash-kernel accepts this generic machine and leaves boot files alone (initramfs updates
    # otherwise fail on the device tree crosvm provides).
    assert files["/etc/flash-kernel/machine"]["content"] == "linux,dummy-virt\n"
