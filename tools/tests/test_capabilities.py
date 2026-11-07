# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""New boundaries: manifests, live capability gates, profiles and guard decisions."""

from copy import deepcopy
import errno
from pathlib import Path
import struct
import pytest
from gunyahvm import capabilities as cap, components, devices, host

ROOT = Path(__file__).resolve().parents[2]


def manifest():
    def component(features, **files):
        return {"version": "test", "features": features, "files": files}

    data = {
        "schema": 1,
        "architecture": "arm64",
        "components": {
            "crosvm": component(
                [
                    "gunyah",
                    "gpu",
                    "runtime-share",
                    "shared-memory",
                    "input-modes",
                    "vnc",
                    "simplefb",
                    "p9",
                ],
                binary="/usr/lib/gunyahvm/crosvm/crosvm",
            ),
            "renderer": component(
                ["virgl", "venus", "drm-msm"],
                library="/usr/lib/gunyahvm/renderer/libvirglrenderer.so.1",
            ),
            "guest_kernel": component(
                ["virtio-gpu", "guest-accept", "shared-boot"],
                image="/usr/share/gunyahvm/guest/Image",
                initrd="/usr/share/gunyahvm/guest/initrd",
            ),
            "guest_mesa": component(
                ["freedreno-virtio"], archive="/usr/share/gunyahvm/guest/mesa.tar.gz"
            ),
        },
    }
    data["components"]["guest_kernel"]["kernel_release"] = "7.4-test"
    data["components"]["guest_mesa"].update(
        guest_abi="ubuntu:26.04",
        library_subdir="lib",
        icd="share/vulkan/icd.d/freedreno.json",
    )
    return data


def capable():
    return cap.Snapshot(
        device_access=True,
        protected_share=True,
        firmware={"hypervisor": True, "capability_ids": {"rm": [1, 2]}},
        runtime=manifest(),
        render_nodes=[
            {"path": "/dev/dri/renderD128", "driver": "msm", "accessible": True}
        ],
        memory={"MemTotal": 16384, "MemAvailable": 12000},
        cpus=[{}] * 8,
        thp="always [advise] never",
    )


def test_share_query_closes_both_fds(monkeypatch):
    calls = []
    monkeypatch.setattr(cap.os, "open", lambda *a: 10)
    monkeypatch.setattr(cap.os, "close", calls.append)

    def ioctl(fd, request, arg):
        if fd == 10:
            assert request == 0x4700 and arg == 0
            return 11
        assert request == 0x4117 and arg == 1
        return 1

    monkeypatch.setattr(cap.fcntl, "ioctl", ioctl)
    assert cap.protected_share("/dev/test") is True
    assert calls == [11, 10]


@pytest.mark.parametrize(
    "error,expected",
    [(errno.ENOTTY, False), (errno.EINVAL, False), (errno.EACCES, None)],
)
def test_extension_unavailable_is_not_guessed(monkeypatch, error, expected):
    closed = []
    monkeypatch.setattr(cap.os, "open", lambda *a: 10)
    monkeypatch.setattr(cap.os, "close", closed.append)

    def ioctl(fd, *_):
        if fd == 10:
            return 11
        raise OSError(error, "fixture")

    monkeypatch.setattr(cap.fcntl, "ioctl", ioctl)
    if expected is None:
        with pytest.raises(OSError):
            cap.protected_share("/dev/test")
    else:
        assert cap.protected_share("/dev/test") is expected
    assert closed == [11, 10]


def test_capability_intersection_degrades_to_virgl():
    snap = capable()
    assert snap.modes() == ["none", "2d", "simplefb", "virgl", "venus", "native"]
    snap.protected_share = False
    assert snap.modes() == ["none", "2d", "simplefb", "virgl"]
    snap.protected_share = None
    assert "venus" not in snap.modes()
    snap.runtime["components"].pop("guest_kernel")
    assert snap.modes() == ["none", "simplefb"]


def test_native_needs_guest_mesa_and_msm_not_board_name():
    snap = capable()
    snap.profile["id"] = "unrelated"
    assert "native" in snap.modes()
    assert "native" not in snap.modes("shared")
    snap.render_nodes[0]["driver"] = "other"
    assert "native" not in snap.modes()
    snap.render_nodes[0]["driver"] = "msm"
    snap.runtime["components"].pop("guest_mesa")
    assert "native" not in snap.modes()


def test_manifest_validates_payload_and_escape(tmp_path):
    data = manifest()
    components.validate(data, check_files=False)
    with pytest.raises(ValueError, match="missing payload"):
        components.validate(data, tmp_path)
    for item in data["components"].values():
        for filename in item["files"].values():
            path = tmp_path / filename.lstrip("/")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
    assert components.validate(data, tmp_path) is data
    data["components"]["crosvm"]["files"]["binary"] = "/usr/lib/gunyahvm/../../bin/sh"
    with pytest.raises(ValueError, match="invalid installed path"):
        components.validate(data, tmp_path)


def test_profiles_match_specific_dt_and_unknown_is_conservative():
    for device in ("piano", "erhai"):
        vendor = "xiaomi" if device == "piano" else "oneplus"
        result = devices.match(
            [vendor + "," + device, "qcom,sm8750"], ROOT / "data/devices"
        )
        assert result["id"] == device
    assert (
        devices.match(["other,tablet", "qcom,sm8750"], ROOT / "data/devices")["id"]
        == "unknown"
    )


def test_ambiguous_and_feature_bearing_profiles_rejected(tmp_path):
    text = (ROOT / "data/devices/piano.toml").read_text()
    (tmp_path / "a.toml").write_text(text)
    (tmp_path / "b.toml").write_text(text)
    with pytest.raises(ValueError, match="ambiguous"):
        devices.match(["xiaomi,piano"], tmp_path)
    data = {
        "schema": 1,
        "id": "test",
        "compatible": ["test,board"],
        "features": {"gpu": True},
    }
    with pytest.raises(ValueError):
        devices.validate(data)


def test_guard_never_treats_charging_as_charge_level():
    profile = deepcopy(devices.DEFAULT)
    readings = {
        "batteries": [{"percent": 9, "status": "Charging"}],
        "temperatures": [{"celsius": 70}],
    }
    assert "Battery" in cap.guard_reasons(profile, readings)[0]
    assert cap.guard_reasons(profile, readings, running=True)
    readings["batteries"][0]["percent"] = 40
    readings["temperatures"][0]["celsius"] = 85
    assert cap.guard_reasons(profile, readings)
    assert not cap.guard_reasons(profile, readings, running=True)


def test_rm_irq_cells_are_read():
    nodes = {
        "/": {"compatible": b"test,board\0"},
        "/hypervisor/rm": {
            "compatible": b"gunyah-resource-manager\0",
            "reg": struct.pack(">QQ", 22, 44),
            "interrupts": struct.pack(">6I", 0, 7, 4, 0, 8, 4),
        },
    }
    assert host.firmware_from_nodes(nodes, "test").interrupts["/hypervisor/rm"] == [
        0,
        7,
        4,
        0,
        8,
        4,
    ]


def test_windows_is_off_without_its_firmware_and_drivers():
    snapshot = capable()
    assert snapshot.modes(guest_os="windows11") == []
    snapshot.runtime["components"]["uefi"] = {"features": ["windows-ecam"]}
    snapshot.runtime["components"]["windows_drivers"] = {}
    assert snapshot.modes(guest_os="windows11") == ["simplefb"]
    snapshot.runtime["components"]["uefi"]["features"] = []
    assert snapshot.modes(guest_os="windows11") == []


@pytest.mark.parametrize("gpu", [830, 0])
def test_msm_identity_uses_kernel_ids_not_board_names(monkeypatch, gpu):
    closed = []
    monkeypatch.setattr(cap.os, "open", lambda *a: 10)
    monkeypatch.setattr(cap.os, "close", closed.append)

    def ioctl(fd, request, buffer):
        assert fd == 10 and request == 0xC0186440
        pipe, parameter, value, length, pad = struct.unpack("=IIQII", buffer)
        assert pipe == 0x10 and value == length == pad == 0
        struct.pack_into("=Q", buffer, 8, gpu if parameter == 1 else 0x08030000)

    monkeypatch.setattr(cap.fcntl, "ioctl", ioctl)
    identity = cap.msm_identity("/dev/dri/renderD128")
    assert identity["gpu_id"] == gpu and identity["chip_id"] == "0x8030000"
    assert identity["model"] == ("Adreno 830" if gpu else "Adreno chip 0x8030000")
    assert closed == [10]
