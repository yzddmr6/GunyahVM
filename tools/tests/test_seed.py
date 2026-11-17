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


def test_seed_reinstalls_guest_files_on_every_boot(tmp_path, monkeypatch):
    import importlib.util
    from pathlib import Path
    from gunyahvm import config, seed

    packed = []

    def iso(argv):
        argv = [str(a) for a in argv]
        packed.extend(Path(a).name for a in argv[argv.index("cidata") + 3:])
        open(argv[argv.index("-output") + 1], "wb").close()

    monkeypatch.setattr(seed, "run", iso)
    cfg = config.complete_new({
        "id": "12345678-1234-4234-8234-123456789abc", "name": "ubuntu", "uid": os.getuid(),
        "gid": os.getgid(), "os": "linux", "cpus": 2, "memory_mib": 2048, "disk_gib": 16,
        "subnet": "10.203.42.0/24", "vhost_net": False, "username": "ubuntu",
        "ssh_public_key": "ssh-ed25519 AAAA test", "source_image": "Ubuntu 26.04 release-20261016",
        "guest_payload": False,
    })
    seed.seed(tmp_path, cfg, cfg["ssh_public_key"])
    user = json.loads((tmp_path / "seed/user-data").read_text().split("\n", 1)[1])
    # cloud-init runs bootcmd on every boot, write_files only for a new instance.
    assert user["bootcmd"] == [["sh", "-c", seed.REFRESH]]
    manifest = json.loads((tmp_path / "seed/guest-files.json").read_text())
    assert [m["path"] for m in manifest] == [f["path"] for f in user["write_files"]]
    assert {"guest-files.json", "gunyahvm-refresh.py"} <= set(packed)
    assert {m["source"] for m in manifest} <= set(packed)
    spec = importlib.util.spec_from_file_location(
        "guest_refresh", tmp_path / "seed/gunyahvm-refresh.py")
    refresh = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(refresh)
    root = tmp_path / "root"
    stale = root / "usr/local/bin/gunyahvm-graphics-run"
    stale.parent.mkdir(parents=True)
    stale.write_text("an older version\n")
    changed = refresh.refresh(tmp_path / "seed", root)
    assert "/usr/local/bin/gunyahvm-graphics-run" in changed
    for item, entry in zip(user["write_files"], manifest):
        target = root / entry["path"].lstrip("/")
        assert target.read_text() == item["content"]
        assert target.stat().st_mode & 0o7777 == int(item["permissions"], 8)
    # Nothing to do on the next boot of the same version.
    assert refresh.refresh(tmp_path / "seed", root) == []
