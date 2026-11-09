# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Behavioural diagnostics, memory policy and live firmware parsing tests."""

from pathlib import Path
import struct
import subprocess
import sys

import pytest
from gunyahvm import host

REPO = Path(__file__).resolve().parents[2]


def put(root, path, value):
    file = root / path
    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_bytes(value)


def tree(root, vmid=19, tx=0xF012345678, rx=0xF087654321):
    put(root, "compatible", b"vendor,board\0qcom,sm8750\0")
    put(root, "hypervisor/compatible", b"gunyah-hypervisor\0")
    put(root, "hypervisor/resource-manager/compatible", b"gunyah-resource-manager\0")
    put(root, "hypervisor/resource-manager/reg", struct.pack(">QQ", tx, rx))
    put(root, "hypervisor/vm/qcom,vmid", struct.pack(">I", vmid))


def make_fdt(nodes):
    names = b"compatible\0reg\0qcom,vmid\0"
    offsets = {
        name: names.index(name.encode() + b"\0")
        for name in ("compatible", "reg", "qcom,vmid")
    }
    output = bytearray()

    def node(name, properties, children):
        output.extend(struct.pack(">I", 1) + name.encode() + b"\0")
        output.extend(b"\0" * (-len(output) % 4))
        for key, value in properties.items():
            output.extend(struct.pack(">III", 3, len(value), offsets[key]) + value)
            output.extend(b"\0" * (-len(output) % 4))
        for args in children:
            node(*args)
        output.extend(struct.pack(">I", 2))

    node(*nodes)
    output.extend(struct.pack(">I", 9))
    start = 56
    return (
        struct.pack(
            ">10I",
            0xD00DFEED,
            start + len(output) + len(names),
            start,
            start + len(output),
            40,
            17,
            16,
            0,
            len(names),
            len(output),
        )
        + b"\0" * 16
        + output
        + names
    )


def test_device_ids_are_read_each_time(tmp_path):
    tree(tmp_path, vmid=7, tx=23, rx=45)
    a = host.firmware(tmp_path, tmp_path / "no-fdt")
    assert a.require().vmids == {"/hypervisor/vm:qcom,vmid": 7}
    assert list(a.capability_ids.values()) == [[23, 45]]
    tree(tmp_path, vmid=99, tx=2**50, rx=2**52)
    b = host.firmware(tmp_path, tmp_path / "no-fdt")
    assert b.vmids != a.vmids
    assert list(b.capability_ids.values()) == [[2**50, 2**52]]


@pytest.mark.parametrize("live_compatible", [b"", b"oneplus,erhai\0qcom,sm8750\0"])
def test_masked_tree_uses_live_fdt(tmp_path, live_compatible):
    fdt = make_fdt(
        (
            "",
            {"compatible": b"vendor,new-board\0"},
            [
                (
                    "hypervisor",
                    {"compatible": b"gunyah-hypervisor\0"},
                    [
                        (
                            "rm",
                            {
                                "compatible": b"gunyah-resource-manager\0",
                                "reg": struct.pack(">QQ", 123, 456),
                            },
                            [],
                        )
                    ],
                )
            ],
        )
    )
    (tmp_path / "fdt").write_bytes(fdt)
    (tmp_path / "tree").mkdir()
    if live_compatible:
        put(tmp_path / "tree", "compatible", live_compatible)
    result = host.firmware(tmp_path / "tree", tmp_path / "fdt").require()
    assert result.source == "live FDT"
    assert result.compatible == (
        host.strings(live_compatible) if live_compatible else ["vendor,new-board"]
    )
    assert list(result.capability_ids.values()) == [[123, 456]]


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\0" * 40,
        struct.pack(">10I", 0xD00DFEED, 40, 99, 100, 40, 17, 16, 0, 3, 5),
    ],
)
def test_malformed_fdt_rejected(data):
    with pytest.raises(ValueError):
        host.parse_fdt(data)


def test_truncated_capabilities_rejected(tmp_path):
    tree(tmp_path)
    put(tmp_path, "hypervisor/resource-manager/reg", struct.pack(">I", 1))
    with pytest.raises(ValueError, match="64-bit"):
        host.firmware(tmp_path, tmp_path / "no-fdt")


def test_defaults_follow_ram_and_online_cpus():
    assert host.defaults("linux", 4096, 1) == {"memory_mib": 1280, "cpus": 1}
    assert host.defaults("linux", 16384, 8) == {"memory_mib": 2048, "cpus": 2}
    assert host.defaults("windows11", 8192, 8) == {"memory_mib": 4608, "cpus": 4}
    with pytest.raises(ValueError, match="Insufficient"):
        host.defaults("windows11", 4096, 8)
    assert host.memory_budget(65536)["memlock_mib"] == 6144
    assert host.memory_budget(1024)["guest_max_mib"] == 0


def test_diagnostics_are_read_only_and_report_all_five(tmp_path, monkeypatch):
    tree(tmp_path / "sys/firmware/devicetree/base")
    for mod in host.MODULES:
        (tmp_path / "sys/module" / mod).mkdir(parents=True)
    put(
        tmp_path,
        "sys/kernel/mm/transparent_hugepage/shmem_enabled",
        b"always [advise] never\n",
    )

    def forbidden(*args, **kwargs):
        raise AssertionError("diagnostics must not mutate or execute")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(host.resource, "setrlimit", forbidden)
    before = {str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    rows = host.diagnostic_rows(tmp_path, (0, 0))
    assert [x[0] for x in rows] == [
        "firmware",
        "modules",
        "device",
        "memlock",
        "hugepages",
    ]
    assert [x[1] for x in rows] == [True, True, False, False, True]
    assert all(x[3] for x in rows)
    assert before == {
        str(p): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()
    }


def test_about_contains_required_attribution():
    output = subprocess.check_output(
        [sys.executable, str(REPO / "bin/gunyahvm"), "about"], text=True
    )
    for value in (
        "1.0.1~rc1",
        "yzddMr6",
        "https://github.com/yzddmr6/GunyahVM",
        "GPL-2.0-only",
        "mzakamin-hub",
        "Droid-VM",
        "ChromiumOS Authors",
        "edk2-gunyah",
        "virtio-win",
        "与 Qualcomm 无关联",
    ):
        assert value in output


def test_no_template_for_current_devices(tmp_path):
    assert host.device_options(["xiaomi,piano", "qcom,sm8750"], tmp_path) == {}
    assert host.device_options(["oneplus,erhai", "qcom,sm8750"], tmp_path) == {}
    with pytest.raises(ValueError):
        host.device_options(["../../escape"], tmp_path)


def test_check_does_not_write_python_cache(tmp_path):
    import os

    env = dict(os.environ, PYTHONPYCACHEPREFIX=str(tmp_path / "bytecode"))
    result = subprocess.run(
        [str(REPO / "bin/gunyahvm"), "check"], env=env, capture_output=True, text=True
    )
    assert result.returncode in (0, 1, 2), result.stderr
    assert all(
        name in result.stdout
        for name in ("firmware", "modules", "device", "memlock", "hugepages")
    )
    assert not (tmp_path / "bytecode").exists()


def test_about_import_and_command_need_no_procfs(monkeypatch, capsys):
    import importlib
    from gunyahvm import cli

    def forbidden(*args, **kwargs):
        raise AssertionError("about must not read host resource state")

    monkeypatch.setattr(host, "memory_budget", forbidden)
    importlib.reload(cli)
    monkeypatch.setattr(sys, "argv", ["gunyahvm", "about"])
    assert cli.main() == 0
    assert "GunyahVM 1.0.1~rc1" in capsys.readouterr().out


def test_missing_procfs_reports_all_diagnostics(tmp_path, monkeypatch):
    def missing():
        raise FileNotFoundError("procfs is absent")

    monkeypatch.setattr(host, "memory_budget", missing)
    rows = host.diagnostic_rows(tmp_path)
    assert len(rows) == 5
    memory = next(row for row in rows if row[0] == "memlock")
    assert not memory[1] and "procfs" in memory[3]


def test_complete_exported_tree_never_reads_restricted_raw_fdt(tmp_path, monkeypatch):
    tree(tmp_path / "sys/firmware/devicetree/base")
    raw = tmp_path / "sys/firmware/fdt"
    original = Path.read_bytes

    def read(path):
        if path == raw:
            raise AssertionError("a complete readable tree must not read raw FDT")
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    row = host.diagnostic_rows(tmp_path, (0, 0))[0]
    assert row[1] is True and "live tree" in row[2]


@pytest.mark.parametrize(
    "error", [PermissionError("Permission denied"), FileNotFoundError("no raw FDT")]
)
def test_unreadable_fallback_is_unknown_not_failed(tmp_path, monkeypatch, error):
    (tmp_path / "sys/firmware/devicetree/base").mkdir(parents=True)
    raw = tmp_path / "sys/firmware/fdt"
    original = Path.read_bytes
    reads = []

    def read(path):
        if path == raw:
            reads.append(path)
            raise error
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", read)
    row = host.diagnostic_rows(tmp_path, (0, 0))[0]
    assert reads == [raw]
    assert row[1] is None
    assert "无法确认" in row[2] and "sudo gunyahvm check" in row[3]


@pytest.mark.parametrize("data", [make_fdt(("", {}, [])), b"not a valid FDT"])
def test_readable_missing_or_invalid_firmware_is_failed(tmp_path, data):
    (tmp_path / "sys/firmware/devicetree/base").mkdir(parents=True)
    put(tmp_path, "sys/firmware/fdt", data)
    assert host.diagnostic_rows(tmp_path, (0, 0))[0][1] is False


@pytest.mark.parametrize("other,expected", [(True, 2), (False, 1)])
def test_check_prints_unknown_and_returns_incomplete_status(
    monkeypatch, capsys, other, expected
):
    monkeypatch.setattr(
        host,
        "diagnostic_rows",
        lambda: [
            ("firmware", None, "无法确认", "read-only root inspection"),
            ("device", other, "device access", "check ACL"),
        ],
    )
    assert host.check() == expected
    result = capsys.readouterr().out
    assert "UNKNOWN firmware: 无法确认" in result
    assert "FAIL firmware" not in result
