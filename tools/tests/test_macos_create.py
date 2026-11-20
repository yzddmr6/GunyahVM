# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""macOS creation: helper jobs, staging, account records, data store and publishing."""
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
from types import SimpleNamespace

import pytest
from gunyahvm import config, macos_create as m

DISK = {"system": {"index": 0, "uuid": "S"}, "data": {"index": 1, "uuid": "D"},
        "preboot": {"index": 2, "uuid": "B"}}


def syntax(script, tmp_path):
    path = tmp_path / "job.sh"
    path.write_text("disk() { :; }\n" + script)
    subprocess.run(["sh", "-n", str(path)], check=True)
    return script


def test_fill_job_is_valid_shell_mounting_one_volume_read_write_at_a_time(tmp_path):
    job = syntax(m.fill_job(DISK, 1581056, 64 << 30), tmp_path)
    assert "--offset 1581056 --sizelimit 68719476736" in job
    lines = [line for line in job.splitlines() if line.startswith(("mount ", "umount "))]
    rw = 0
    for line in lines:
        if line.startswith("mount ") and "readwrite" in line:
            rw += 1
            assert rw == 1, "two volumes mounted read-write"
        if line.startswith("umount /m/v"):
            rw = 0
    assert "--exclude=./System/Library/dyld/dyld_shared_cache_x86_64*" in job


def test_stage_job_quotes_destinations(tmp_path):
    staging = m.Staging()
    staging.put("Library/Managed Preferences/odd 'name'.plist", b"x")
    job = syntax(m.stage_job(DISK, 4096, 1 << 30, staging.ops), tmp_path)
    assert "'Library/Managed Preferences/odd '\"'\"'name'\"'\"'.plist'" in job
    assert "vol=1" in job


def test_account_records_add_the_user_to_admin_and_staff():
    user, guid = m.user_record("tester", "pw")
    record = plistlib.loads(user)
    assert record["name"] == ["tester"] and record["uid"] == ["501"] and record["generateduid"] == [guid]
    shadow = plistlib.loads(record["ShadowHashData"][0])["SALTED-SHA512-PBKDF2"]
    assert hashlib.pbkdf2_hmac("sha512", b"pw", shadow["salt"], shadow["iterations"], 128) == shadow["entropy"]
    admin = plistlib.dumps({"name": ["admin"], "users": ["root"], "groupmembers": ["X"]}, fmt=plistlib.FMT_BINARY)
    staff = plistlib.dumps({"name": ["staff"], "users": ["root"]}, fmt=plistlib.FMT_BINARY)
    groups = m.group_records("tester", guid, admin, staff)
    assert plistlib.loads(groups["admin"])["users"] == ["root", "tester"]
    assert plistlib.loads(groups["admin"])["groupmembers"] == ["X", guid]
    assert plistlib.loads(groups["staff"])["groupmembers"] == [guid]


@pytest.mark.parametrize("name", ["root", "Tester", "1user", "a b", ""])
def test_invalid_account_names(name):
    with pytest.raises(m.Failed):
        m.valid_account(name)


def fake_macho(tmp_path, payload):
    """A thin Mach-O with an embedded SHA-256 CodeDirectory."""
    cd = struct.pack(">II", 0xFADE0C02, 0) + bytes(29) + bytes([2]) + payload
    cd = struct.pack(">II", 0xFADE0C02, len(cd)) + cd[8:]
    blob = struct.pack(">III", 0xFADE0CC0, 0, 1) + struct.pack(">II", 0, 20) + cd
    header = struct.pack("<8I", 0xFEEDFACF, 0x100000C, 0, 6, 1, 16, 0, 0)
    sig_offset = 64
    command = struct.pack("<IIII", 0x1D, 16, sig_offset, len(blob))
    data = header + command + bytes(sig_offset - 32 - 16) + blob
    path = tmp_path / ("bin-" + payload.hex())
    path.write_bytes(data)
    return path, hashlib.sha256(cd).digest()[:20]


def test_cdhash_and_trust_bundle(tmp_path):
    path, expected = fake_macho(tmp_path, b"code")
    assert m.cdhash(path) == expected
    plain = tmp_path / "Info.plist"
    plain.write_bytes(b"<plist/>")
    assert m.cdhash(plain) is None
    module = struct.pack("<I16sI", 2, bytes(16), 1) + bytes(24)
    bundle = m.trust_bundle([module, module])
    count, first, second = struct.unpack_from("<3I", bundle)
    assert count == 2 and bundle[first:second] == module


def test_new_disk_is_a_gpt_disk_and_the_seed_is_empty(tmp_path):
    from gunyahvm import macos_disk
    logical = 256 << 20
    offset = m.create_store(tmp_path / "disk.raw", logical)
    assert offset == 1 << 20
    assert macos_disk.container(tmp_path / "disk.raw") == (offset, logical)
    data, identity = m.seed(logical)
    assert data[:8] == b"DBRDISK1" and len(data) == 32


class FakeKit:
    manifest = {"version": "test", "files": {}}

    def verify(self):
        pass


def creation(tmp_path, monkeypatch, **kw):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    return m.Creation(home / "store", "mac", "tester", "pw", kit=FakeKit(), **kw)


def test_publish_moves_the_finished_vm_into_the_library(tmp_path, monkeypatch):
    c = creation(tmp_path, monkeypatch)
    c.preflight = lambda: None
    c.work.mkdir(parents=True)
    c._load()
    vm = c.work / "vm"
    vm.mkdir()
    for name in ("disk.raw", "boot.img", "agent-key"):
        (vm / name).write_bytes(name.encode())
    c.state["done"]["boot"] = {"sha256": hashlib.sha256(b"boot.img").hexdigest()}
    target = c.publish(vm)
    assert target == c.store / "vms/mac" and not c.work.exists()
    cfg = config.validate(json.loads((target / "config.json").read_text()))
    assert cfg["os"] == "macos" and cfg["macos"] == m.PROFILE and cfg["disk_gib"] == 48
    assert json.loads((target / "inputs.json").read_text())["created"] == "apple-ipsw"
    assert all(p.stat().st_mode & 0o777 == 0o600 for p in target.iterdir())


def test_preflight_refuses_an_existing_vm_and_outside_home(tmp_path, monkeypatch):
    c = creation(tmp_path, monkeypatch)
    (c.store / "vms/mac").mkdir(parents=True)
    with pytest.raises(m.Failed, match="already exists"):
        c.preflight()
    outside = m.Creation(tmp_path / "elsewhere", "mac", "tester", "pw", kit=FakeKit())
    with pytest.raises(m.Failed, match="home directory"):
        outside.preflight()


def test_resume_keeps_settings_and_never_needs_the_password_again(tmp_path, monkeypatch):
    c = creation(tmp_path, monkeypatch)
    c.work.mkdir(parents=True)
    c._load()
    c.mark("account", guid="G")
    assert m.saved_request(c.store, "mac") == {"account": "tester", "disk_gib": 48, "keep_downloads": False}
    other = m.Creation(c.store, "mac", "someone", None, kit=FakeKit())
    with pytest.raises(m.Failed, match="other settings"):
        other._load()
    assert m.pending(c.store) == ["mac"]
    assert m.discard(c.store, "mac") and m.pending(c.store) == []


def test_helper_status_comes_from_the_exchange_disk(tmp_path):
    exchange = tmp_path / "exchange.img"
    m.fat_image(exchange, 16, {"job.sh": b"true\n", "status": b"0\n", "log.txt": b"step done\n"})
    m.check_helper(tmp_path, [("gvm-exchange", exchange, False)], "fill", 0)
    m.fat_image(exchange, 16, {"job.sh": b"true\n", "status": b"1\n", "log.txt": b"cp: failed\n"})
    with pytest.raises(m.Failed, match="cp: failed"):
        m.check_helper(tmp_path, [("gvm-exchange", exchange, False)], "fill", 0)


def test_helper_init_script_is_valid_shell():
    script = Path(m.__file__).with_name("macos-helper-init")
    subprocess.run(["sh", "-n", str(script)], check=True)
    assert "poweroff" in script.read_text()


def test_cli_reads_the_password_from_a_descriptor_and_discards(tmp_path, monkeypatch):
    from gunyahvm import cli
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1000)
    seen = {}

    class Fake:
        def __init__(self, store, name, account, password, disk_gib, progress, keep_downloads):
            seen.update(name=name, account=account, password=password, disk_gib=disk_gib,
                        keep=keep_downloads)

        def run(self):
            return "done"
    monkeypatch.setattr(m, "Creation", Fake)
    read, write = os.pipe()
    os.write(write, b"s3cret\n")
    os.close(write)
    assert cli.main(["--store", str(tmp_path), "create-macos", "mac", "--account", "tester",
                     "--password-fd", str(read), "--disk-gib", "80", "--keep-downloads"]) == 0
    assert seen == {"name": "mac", "account": "tester", "password": "s3cret", "disk_gib": 80, "keep": True}
    work = tmp_path / ".creating/mac"
    work.mkdir(parents=True)
    (work / "state.json").write_text("{}")
    assert cli.main(["--store", str(tmp_path), "create-macos", "mac", "--discard"]) == 0
    assert not work.exists()


def old_vm(tmp_path, monkeypatch, kit="1.5.0-1"):
    """A VM as GunyahVM 1.5.0 left it: a DroidVMDataStore disk and the old kit's boot disk."""
    from test_macos_disk import store
    home = tmp_path / "home"
    vm = home / "store/vms/mac"
    vm.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", lambda: home)
    offset = store(vm / "disk.raw", 512 << 20, {0: b"NXSB".ljust(4096, b"n")})
    (vm / "boot.img").write_bytes(b"old boot")
    (vm / "inputs.json").write_text(json.dumps({"schema": 4, "kit": kit, "files": {"boot.img": "old"}}))
    return vm, offset


class NewKit(FakeKit):
    manifest = {"version": "1.6.0-1", "files": {}}


def upgrade_fakes(monkeypatch, calls):
    monkeypatch.setattr(m.Creation, "fetch", lambda self, name, a, b: calls.append(("fetch", name)))
    monkeypatch.setattr(m, "apple_kernel_collection", lambda src, out: Path(out).write_bytes(b"apple kc"))
    monkeypatch.setattr(m, "link_kernel_collection", lambda kit, apple, out, log: Path(out).write_bytes(b"new kc"))
    monkeypatch.setattr(m, "trust_module", lambda path: b"")
    monkeypatch.setattr(m, "trust_bundle", lambda modules: b"stock")
    monkeypatch.setattr(m.macos_inputs, "STOCK_TRUST_SHA256", hashlib.sha256(b"stock").hexdigest())
    monkeypatch.setattr(m, "boot_trust", lambda kit, stock: b"trust")
    monkeypatch.setattr(m, "container_volumes", lambda store, offset, logical: {"data": {"index": 1}})
    monkeypatch.setattr(m, "kit_staging", lambda kit: SimpleNamespace(ops=["cp plugin"], files={"p": b"x"}))
    monkeypatch.setattr(m, "make_adt", lambda kit, out: Path(out).write_bytes(b"adt"))
    monkeypatch.setattr(m, "boot_disk", lambda kit, kc, trust, adt, data, out: Path(out).write_bytes(b"new boot"))


def test_upgrade_converts_restages_and_relinks_an_old_vm(tmp_path, monkeypatch):
    from gunyahvm import macos_disk
    vm, offset = old_vm(tmp_path, monkeypatch)
    calls = []
    upgrade_fakes(monkeypatch, calls)
    staged = []

    def helper(work, disks, phase, upgrade):
        assert Path(work).parent.name == ".creating" and phase == "stage"
        assert dict((s, p) for s, p, _ in disks)["gvm-store"] == vm / "disk.raw"
        # The disk is already the GPT disk the new kit's drivers read.
        staged.append(macos_disk.container(vm / "disk.raw"))
    assert m.needs_upgrade(vm, NewKit())
    m.Upgrade(vm, kit=NewKit(), helper=helper).run()
    assert staged == [(offset, 512 << 20)]
    assert [c for c in calls if c[0] == "fetch"] == [("fetch", n) for n in
                                                    ("kernelcache", "os-trustcache", "system-trustcache", "app-trustcache")]
    assert (vm / "boot.img").read_bytes() == b"new boot"
    manifest = json.loads((vm / "inputs.json").read_text())
    assert manifest["kit"] == "1.6.0-1"
    assert manifest["files"]["boot.img"] == hashlib.sha256(b"new boot").hexdigest()
    assert not m.needs_upgrade(vm, NewKit())
    assert not (vm.parent.parent / ".creating/.upgrade-mac").exists()
    with open(vm / "disk.raw", "rb") as f:
        f.seek(offset)
        assert f.read(4) == b"NXSB"


def test_a_failed_upgrade_puts_the_store_back_and_the_next_start_completes(tmp_path, monkeypatch):
    from gunyahvm import macos_disk
    vm, offset = old_vm(tmp_path, monkeypatch)
    before = (vm / "disk.raw").read_bytes()[:offset], (vm / "disk.raw").stat().st_size
    calls = []
    upgrade_fakes(monkeypatch, calls)
    runs = []

    def failing_helper(work, disks, phase, upgrade):
        runs.append(macos_disk.container(vm / "disk.raw"))
        if len(runs) == 1:  # the helper VM's crosvm killed
            raise m.Failed("helper lost")
    with pytest.raises(m.Failed, match="helper lost.*restored as it was"):
        m.Upgrade(vm, kit=NewKit(), helper=failing_helper).run()
    assert ((vm / "disk.raw").read_bytes()[:offset], (vm / "disk.raw").stat().st_size) == before
    assert (vm / "boot.img").read_bytes() == b"old boot" and m.needs_upgrade(vm, NewKit())
    assert m.pending(vm.parent.parent) == []
    m.Upgrade(vm, kit=NewKit(), helper=failing_helper).run()
    assert runs == [(offset, 512 << 20)] * 2  # converted again, staged on the GPT disk
    assert sum(1 for c in calls if c == ("fetch", "kernelcache")) == 1  # nothing fetched twice
    assert (vm / "boot.img").read_bytes() == b"new boot" and not m.needs_upgrade(vm, NewKit())


@pytest.mark.parametrize("dies_in", ["inputs", "kc", "trust", "boot", "disk", "stage", "commit"])
def test_an_upgrade_killed_at_any_step_resumes_on_the_next_start(tmp_path, monkeypatch, dies_in):
    """The process dies (power cut, VMM killed with its supervisor): no handler runs, nothing is
    rolled back; the next start continues from the recorded steps."""
    from gunyahvm import macos_disk
    vm, offset = old_vm(tmp_path, monkeypatch)
    calls = []
    upgrade_fakes(monkeypatch, calls)

    class PowerCut(BaseException):
        pass
    hooks = {"inputs": (m.Creation, "fetch"), "kc": (m, "link_kernel_collection"), "trust": (m, "boot_trust"),
             "boot": (m, "boot_disk"), "disk": (macos_disk, "convert"), "stage": None, "commit": (m.os, "replace")}

    def cut(*a, **k):
        raise PowerCut()
    helper = (lambda *a: (_ for _ in ()).throw(PowerCut())) if dies_in == "stage" else (lambda *a: None)
    with monkeypatch.context() as patch:
        if hooks[dies_in]:
            owner, name = hooks[dies_in]
            real = getattr(owner, name)
            if dies_in == "commit":  # the boot disk's rename, the last change
                patch.setattr(owner, name, lambda a, b: cut() if str(b).endswith("vms/mac/boot.img") else real(a, b))
            else:
                patch.setattr(owner, name, cut)
        with pytest.raises(PowerCut):
            m.Upgrade(vm, kit=NewKit(), helper=helper).run()
    m.Upgrade(vm, kit=NewKit(), helper=lambda *a: None).run()
    assert macos_disk.container(vm / "disk.raw") == (offset, 512 << 20)
    assert (vm / "boot.img").read_bytes() == b"new boot" and not m.needs_upgrade(vm, NewKit())
    with open(vm / "disk.raw", "rb") as f:
        f.seek(offset)
        assert f.read(4) == b"NXSB"
