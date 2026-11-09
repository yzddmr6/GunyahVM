# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Generated Windows payload must agree with the renamed compiled helper."""

import pytest
from xml.etree import ElementTree as ET

from gunyahvm.windows import windows_deploy


def test_windows_payload_names_and_standard_oobe(tmp_path):
    token = "12345678-1234-4123-8123-123456789abc"
    windows_deploy.write_payload(tmp_path, 2, None, token)
    xml = ET.parse(tmp_path / "unattend.xml")
    ns = {"u": windows_deploy.NS}
    assert len(xml.find(".//u:ComputerName", ns).text) <= 15
    assert not xml.findall(".//u:AutoLogon", ns)
    assert not xml.findall(".//u:LocalAccount", ns)
    assert xml.find(".//u:HideWirelessSetupInOOBE", ns) is None
    text = "\n".join(p.read_text() for p in tmp_path.iterdir() if p.is_file())
    assert "piano" not in text.lower()
    assert "C:\\gunyahvm\\scratch" in text
    assert "GUNYAHVM_DEPLOYED.TAG" in text
    assert token in text
    assert "C:\\gunyahvm" in text and "X:\\gunyahvm" in text


@pytest.mark.inputs
def test_compiled_helper_uses_current_guest_paths(verified_inputs):
    helper = (verified_inputs / "windows/winpe-helper.exe").read_bytes()
    for path in (
        r"C:\gunyahvm\DroidVM_Test.cer",
        r"X:\gunyahvm\DroidVM_Test.cer",
        r"C:\gunyahvm\drivers\pvmpower\pvmpower.inf",
    ):
        assert path.encode("utf-16le") in helper
    assert b"P\x00i\x00a\x00n\x00o\x00" not in helper
    assert helper[:2] == b"MZ"


def test_password_is_only_in_explicit_account_payload(tmp_path):
    windows_deploy.write_payload(
        tmp_path,
        1,
        {"username": "guestuser", "password": "example-not-a-secret"},
        "12345678-1234-4123-8123-123456789abc",
    )
    xml = ET.parse(tmp_path / "unattend.xml")
    ns = {"u": windows_deploy.NS}
    assert xml.find(".//u:LocalAccount/u:Name", ns).text == "guestuser"
    assert xml.find(".//u:HideOnlineAccountScreens", ns).text == "true"
    assert xml.find(".//u:HideWirelessSetupInOOBE", ns).text == "true"
    assert "example-not-a-secret" not in (tmp_path / "unattend.xml").read_text()
    assert xml.find(".//u:Password/u:PlainText", ns).text == "false"


def test_windows_edition_id_matches_localized_wim_name():
    import importlib.util
    import sys
    from pathlib import Path
    source = Path(windows_deploy.__file__).with_name("prepare-windows.py")
    sys.path.insert(0, str(source.parent))
    try:
        spec = importlib.util.spec_from_file_location("gvm_prepare_windows", source)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        images = [{"index": 3, "name": "Windows 11 Pro", "edition": "Professional", "build": "26300"}]
        assert module.choose(images, "Professional") == images[0]
        assert module.choose(images, "3") == images[0]
        assert module.choose(images, "Windows 11 Pro") == images[0]
    finally:
        sys.path.pop(0)
