# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
"""Offline payload integrity, real media roundtrip and executable PS preflight."""

import hashlib
import importlib.util
import json
import re
import shutil
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from gunyahvm.windows import windows_deploy
from prebuilt_inputs import fetch
from windows_openssh import stage_openssh, verify_openssh

REPO = Path(__file__).resolve().parents[2]
WINDOWS = REPO / "src/gunyahvm/windows"
LOCK = json.loads((REPO / "tools/windows.lock.json").read_text())["openssh"]


@pytest.fixture
def preparation():
    sys.path.insert(0, str(WINDOWS))
    try:
        spec = importlib.util.spec_from_file_location(
            "prepare_windows_offline_test", WINDOWS / "prepare-windows.py"
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.path.pop(0)


def test_missing_openssh_never_downloads(tmp_path, monkeypatch, preparation):
    def forbidden(*args, **kwargs):
        pytest.fail("offline input validation attempted a network download")

    monkeypatch.setattr("urllib.request.urlopen", forbidden)
    with pytest.raises(ValueError, match="Missing offline input"):
        fetch(LOCK, tmp_path, offline=True)
    with pytest.raises(ValueError, match="Missing offline OpenSSH"):
        preparation.verify_openssh(tmp_path)


def test_fresh_asset_build_checks_offline_input_before_compiling(tmp_path):
    output = tmp_path / "build"
    result = subprocess.run(
        [sys.executable, str(REPO / "tools/build-windows-assets.py"),
         "--inputs", str(tmp_path), "--firmware-cache", str(tmp_path),
         "--output", str(output)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "Missing offline OpenSSH archive" in result.stderr
    assert not output.exists()


@pytest.mark.inputs
def test_pinned_arm64_payload_and_notices(verified_inputs):
    assets = verified_inputs / "windows"
    verify_openssh(assets, LOCK)
    assert LOCK["url"] == (
        "https://github.com/PowerShell/Win32-OpenSSH/releases/download/"
        + LOCK["version"] + "/OpenSSH-ARM64.zip"
    )
    with zipfile.ZipFile(assets / LOCK["filename"]) as archive:
        for name in ("sshd.exe", "sshd-auth.exe", "sshd-session.exe", "ssh-keygen.exe"):
            binary = archive.read("OpenSSH-ARM64/" + name)
            assert binary[:2] == b"MZ"
            offset = struct.unpack_from("<I", binary, 0x3C)[0]
            assert binary[offset:offset + 4] == b"PE\0\0"
            assert struct.unpack_from("<H", binary, offset + 4)[0] == 0xAA64
        for name in ("LICENSE.txt", "NOTICE.txt"):
            assert (assets / "licenses/openssh" / name).read_bytes() == archive.read(
                "OpenSSH-ARM64/" + name
            )
        # Audit the unmodified upstream installer and its imported module too.
        for name in ("install-sshd.ps1", "OpenSSHUtils.psm1", "OpenSSHUtils.psd1"):
            assert_no_online_install(archive.read("OpenSSH-ARM64/" + name).decode("utf-8-sig"))


@pytest.mark.inputs
def test_openssh_survives_actual_preparation_media(
    tmp_path, verified_inputs, preparation
):
    payload = tmp_path / "payload"
    payload.mkdir()
    preparation.copy_openssh(verified_inputs / "windows", payload)
    windows_deploy.write_payload(
        payload, 1, {"username": "vmuser", "password": "test-password"},
        "12345678-1234-4123-8123-123456789abc", agent=True,
    )
    image = tmp_path / "drivers.raw"
    preparation.fat_disk(image, payload, 64 * 1024**2, "GUNYAHVMDRV")
    restored = tmp_path / "restored"
    restored.mkdir()
    subprocess.run(
        ["mcopy", "-s", "-i", str(image) + "@@1048576", "::*", str(restored)],
        check=True, capture_output=True,
    )
    preparation.verify_openssh(restored)
    for name in ("OpenSSH-ARM64.zip", "openssh.json", "install-openssh.ps1", "agent-setup.ps1"):
        assert (restored / name).read_bytes() == (payload / name).read_bytes()
    assert hashlib.sha256((restored / LOCK["filename"]).read_bytes()).hexdigest() == LOCK["sha256"]


@pytest.mark.inputs
def test_bad_openssh_cannot_enter_package_or_media(
    tmp_path, verified_inputs, preparation
):
    archive = tmp_path / LOCK["filename"]
    archive.write_bytes(b"corrupted release")
    with pytest.raises(ValueError, match="checksum mismatch"):
        stage_openssh(archive, tmp_path / "staged", LOCK)
    assert not (tmp_path / "staged").exists()
    (tmp_path / "openssh.json").write_text(json.dumps(LOCK))
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        preparation.verify_openssh(tmp_path)
    shutil.copyfile(verified_inputs / "windows" / LOCK["filename"], archive)
    wrong = dict(LOCK, version="unlocked")
    (tmp_path / "openssh.json").write_text(json.dumps(wrong))
    with pytest.raises(ValueError, match="manifest differs"):
        verify_openssh(tmp_path, LOCK)


def assert_no_online_install(text):
    # URLs in comments/provenance are harmless; commands that fetch are not.
    code = "\n".join(line for line in text.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(
        r"(?i)\b(?:Add-WindowsCapability|Get-WindowsCapability|Enable-WindowsOptionalFeature|"
        r"Install-(?:Module|Package|Script)|Save-Module|Invoke-WebRequest|Invoke-RestMethod|"
        r"Start-BitsTransfer|DownloadString|DownloadFile|winget|curl|wget)\b", code
    )


@pytest.mark.parametrize("account", [None, {"username": "vmuser", "password": "test-password"}])
def test_first_configuration_has_no_online_install(tmp_path, account):
    windows_deploy.write_payload(tmp_path, 1, account, "12345678", agent=True)
    scripts = list(tmp_path.glob("*.ps1")) + list(tmp_path.glob("*.cmd"))
    scripts += [WINDOWS / "agent-setup.ps1", WINDOWS / "install-openssh.ps1"]
    for script in scripts:
        assert_no_online_install(script.read_text())
    bootstrap = (tmp_path / "bootstrap.cmd").read_text()
    assert "wpeutil InitializeNetwork" not in bootstrap
    assert r"wpeinit -unattend:X:\gunyahvm\winpe-unattend.xml" in bootstrap
    xml = windows_deploy.ET.parse(tmp_path / "winpe-unattend.xml")
    assert xml.findtext(".//{" + windows_deploy.NS + "}EnableNetwork") == "false"
    agent = (WINDOWS / "agent-setup.ps1").read_text()
    for setting in ("AuthenticationMethods publickey", "PasswordAuthentication no", "KbdInteractiveAuthentication no"):
        assert setting in agent


@pytest.fixture
def powershell():
    program = shutil.which("pwsh")
    if not program:
        pytest.skip("install PowerShell or add pwsh to PATH for executable preflight tests")
    return program


@pytest.mark.inputs
@pytest.mark.parametrize("damage,message", [
    (None, "GUNYAHVM_OPENSSH_VERIFIED"),
    ("archive", "Missing offline OpenSSH archive"),
    ("manifest", "Missing offline OpenSSH manifest"),
    ("corrupt", "Offline OpenSSH SHA256 mismatch"),
    ("architecture", "Invalid offline OpenSSH ARM64 manifest"),
])
def test_powershell_preflight_executes_offline(
    tmp_path, verified_inputs, powershell, damage, message
):
    for name in ("OpenSSH-ARM64.zip", "openssh.json"):
        shutil.copyfile(verified_inputs / "windows" / name, tmp_path / name)
    if damage == "archive":
        (tmp_path / "OpenSSH-ARM64.zip").unlink()
    elif damage == "manifest":
        (tmp_path / "openssh.json").unlink()
    elif damage == "corrupt":
        (tmp_path / "OpenSSH-ARM64.zip").write_bytes(b"broken")
    elif damage == "architecture":
        (tmp_path / "openssh.json").write_text(json.dumps(dict(LOCK, architecture="x64")))
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
         str(WINDOWS / "install-openssh.ps1"), "-PayloadRoot", str(tmp_path), "-CheckOnly"],
        capture_output=True, text=True, timeout=20,
    )
    assert (result.returncode == 0) == (damage is None), result.stdout + result.stderr
    assert message in result.stdout + result.stderr
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir()}


def test_powershell_rejects_remote_payload_before_access(powershell):
    result = subprocess.run(
        [powershell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File",
         str(WINDOWS / "install-openssh.ps1"), "-PayloadRoot", r"\\invalid\payload", "-CheckOnly"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    assert "requires a local payload directory" in result.stderr


def test_powershell_parses_all_first_boot_scripts(tmp_path, powershell):
    windows_deploy.write_payload(tmp_path, 1, None, "12345678", agent=True)
    for name in ("agent-setup.ps1", "install-openssh.ps1"):
        shutil.copyfile(WINDOWS / name, tmp_path / name)
    script = tmp_path / "parse.ps1"
    script.write_text("""
$ErrorActionPreference = 'Stop'
Get-ChildItem -LiteralPath $PSScriptRoot -Filter *.ps1 | ForEach-Object {
    $tokens = $null; $errors = $null
    $null = [System.Management.Automation.Language.Parser]::ParseFile($_.FullName, [ref]$tokens, [ref]$errors)
    if ($errors.Count) { throw ($errors | Out-String) }
}
""")
    subprocess.run([powershell, "-NoProfile", "-File", str(script)], check=True, timeout=20)


@pytest.mark.parametrize("code,finished,accepted", [
    ("0", True, True), ("3010", True, True), ("1", True, False),
    ("$null", True, False), ("0", False, False),
])
def test_installer_retains_exit_status_and_rejects_failure(
    tmp_path, powershell, code, finished, accepted
):
    # Execute the production process-management block against the PS 5.1
    # handle-lifetime contract. Without a retained handle, waiting loses status.
    source = (WINDOWS / "install-openssh.ps1").read_text()
    block = source[source.index("$process = Start-Process"):source.index("$service = Get-CimInstance")]
    script = tmp_path / "exit-status.ps1"
    script.write_text(r"""
$ErrorActionPreference = 'Stop'
$PayloadRoot = $PSScriptRoot
$powershell = 'unused'; $installer = 'unused'
$child = [pscustomobject]@{ ExitCode=CODE; Pinned=$false; Killed=$false; Disposed=$false }
$child | Add-Member ScriptProperty Handle { $this.Pinned=$true; return 42 }
$child | Add-Member ScriptMethod WaitForExit {
    if (-not $this.Pinned) { $this.ExitCode=$null }
    if ($args.Count) {
        if ($args[0] -ne 90000) { throw 'timeout contract changed' }
        return FINISHED
    }
}
$child | Add-Member ScriptMethod Kill { $this.Killed=$true }
$child | Add-Member ScriptMethod Dispose { $this.Disposed=$true }
function Start-Process { return $child }
$accepted=$true
try {
""".replace("CODE", code).replace("FINISHED", "$true" if finished else "$false")
        + block + r"""
} catch { $accepted=$false; Write-Output $_ }
[pscustomobject]@{ Accepted=$accepted; Pinned=$child.Pinned; Killed=$child.Killed; Disposed=$child.Disposed } | ConvertTo-Json -Compress
""")
    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(script)],
        check=True, capture_output=True, text=True, timeout=10,
    )
    outcome = json.loads(result.stdout.strip().splitlines()[-1])
    assert outcome == {"Accepted": accepted, "Pinned": True,
                       "Killed": not finished, "Disposed": True}
