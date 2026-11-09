# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# CheckOnly is portable and read-only; installation requires Windows ARM64/admin.
[CmdletBinding()]
param (
    [string]$PayloadRoot = $PSScriptRoot,
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0

# Reject remote paths before any filesystem access (including SMB/WebDAV).
if ($PayloadRoot -match '^[\\/]{2}' -or $PayloadRoot -match '://') {
    throw 'Offline OpenSSH requires a local payload directory'
}
$manifest = Join-Path $PayloadRoot 'openssh.json'
$archive = Join-Path $PayloadRoot 'OpenSSH-ARM64.zip'
if (-not (Test-Path -LiteralPath $manifest -PathType Leaf)) {
    throw 'Missing offline OpenSSH manifest: openssh.json; recreate the Windows media'
}
$spec = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
if ($spec.filename -cne 'OpenSSH-ARM64.zip' -or $spec.architecture -cne 'arm64' -or $spec.sha256 -cnotmatch '^[0-9a-f]{64}$') {
    throw 'Invalid offline OpenSSH ARM64 manifest; recreate the Windows media'
}
if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
    throw 'Missing offline OpenSSH archive: OpenSSH-ARM64.zip; recreate the Windows media'
}
$actual = (Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash.ToLowerInvariant()
if ($actual -cne $spec.sha256) {
    throw "Offline OpenSSH SHA256 mismatch: expected $($spec.sha256), got $actual; recreate the Windows media"
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [IO.Compression.ZipFile]::OpenRead($archive)
try {
    $names = @($zip.Entries | ForEach-Object { $_.FullName })
    foreach ($name in @('install-sshd.ps1', 'OpenSSHUtils.psm1', 'OpenSSHUtils.psd1', 'openssh-events.man', 'sshd.exe', 'sshd-auth.exe', 'sshd-session.exe', 'ssh-keygen.exe', 'ssh-agent.exe', 'sftp-server.exe', 'libcrypto.dll', 'LICENSE.txt', 'NOTICE.txt')) {
        if (@($names | Where-Object { $_ -ceq "OpenSSH-ARM64/$name" }).Count -ne 1) {
            throw "Offline OpenSSH archive is incomplete: $name"
        }
    }
    foreach ($name in $names) {
        if (-not $name.StartsWith('OpenSSH-ARM64/') -or $name -match '(^|/)\.\.(/|$)|[:\\]') {
            throw "Unsafe offline OpenSSH archive member: $name"
        }
    }
} finally {
    $zip.Dispose()
}
Write-Output "GUNYAHVM_OPENSSH_VERIFIED $($spec.version) SHA256=$actual"
if ($CheckOnly) { return }

if ($env:PROCESSOR_ARCHITECTURE -ne 'ARM64') {
    throw 'Offline OpenSSH installation requires native Windows ARM64 PowerShell'
}
$destination = Join-Path $env:ProgramFiles 'GunyahVM'
$installed = Join-Path $destination 'OpenSSH-ARM64'
# Stop before overwriting binaries, including when retrying interrupted setup.
foreach ($name in @('sshd', 'ssh-agent')) {
    $service = Get-Service -Name $name -ErrorAction SilentlyContinue
    if ($service -and $service.Status -ne 'Stopped') {
        $service.Stop()
        $service.WaitForStatus('Stopped', [TimeSpan]::FromSeconds(30))
    }
}
New-Item -Path $destination -ItemType Directory -Force | Out-Null
Expand-Archive -LiteralPath $archive -DestinationPath $destination -Force
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$installer = Join-Path $installed 'install-sshd.ps1'
# Use the unmodified upstream service/ACL installer. Bypass avoids online
# Authenticode revocation checks; integrity was checked against our pinned hash.
$process = Start-Process -FilePath $powershell -ArgumentList "-NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$installer`"" -PassThru -NoNewWindow -RedirectStandardOutput (Join-Path $PayloadRoot 'openssh-install.log') -RedirectStandardError (Join-Path $PayloadRoot 'openssh-install-error.log')
# Windows PowerShell 5.1 can lose the exit code when WaitForExit opens and
# closes a temporary handle. Retain the native handle before waiting.
$processHandle = $process.Handle
try {
    if (-not $process.WaitForExit(90000)) {
        $process.Kill()
        throw 'Offline OpenSSH service installation timed out after 90 seconds; see openssh-install*.log'
    }
    $process.WaitForExit()
    $exitCode = $process.ExitCode
    if ($exitCode -notin @(0, 3010)) {
        throw "Offline OpenSSH service installation failed ($exitCode); see openssh-install*.log"
    }
} finally {
    $process.Dispose()
}
$service = Get-CimInstance Win32_Service -Filter "Name='sshd'"
if (-not $service -or $service.PathName -cne ('"' + (Join-Path $installed 'sshd.exe') + '"')) {
    throw 'Offline OpenSSH service does not point to the bundled sshd.exe'
}
Write-Output "GUNYAHVM_OPENSSH_INSTALLED EXIT_CODE=$exitCode"
