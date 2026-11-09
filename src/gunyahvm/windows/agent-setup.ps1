# SPDX-License-Identifier: GPL-2.0-only
# Copyright (C) 2026 yzddMr6
# Key-only control adapted from iceland-vms WinAgent (mzakamin-hub)
# d628b9b25d08e074b34a2f1a6113ab23767b78d7.
$ErrorActionPreference = 'Stop'
& (Join-Path $PSScriptRoot 'install-openssh.ps1') -CheckOnly
$settings = Get-Content C:\gunyahvm\agent.json -Raw | ConvertFrom-Json
if (-not (Get-LocalUser -Name $settings.user -ErrorAction SilentlyContinue)) {
    $bytes = New-Object byte[] 48
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $password = ConvertTo-SecureString ([Convert]::ToBase64String($bytes) + 'aA1!') -AsPlainText -Force
    New-LocalUser -Name $settings.user -Password $password -PasswordNeverExpires -AccountNeverExpires | Out-Null
}
# Hide only the sign-in tile; the enabled account retains SSH/network logon.
$userList = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon\SpecialAccounts\UserList'
New-Item -Path $userList -Force | Out-Null
New-ItemProperty -Path $userList -Name $settings.user -Value 0 -PropertyType DWord -Force | Out-Null
$admins = Get-LocalGroup -SID 'S-1-5-32-544'
Add-LocalGroupMember -Group $admins -Member $settings.user -ErrorAction SilentlyContinue
New-Item C:\ProgramData\ssh -ItemType Directory -Force | Out-Null
$keyFile = 'C:\ProgramData\ssh\administrators_authorized_keys'
Set-Content -LiteralPath $keyFile -Value $settings.key -Encoding ascii
icacls $keyFile /inheritance:r /grant '*S-1-5-32-544:F' /grant '*S-1-5-18:F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'SSH key ACL failed' }
$config = @"
Port 22
PubkeyAuthentication yes
AuthenticationMethods publickey
PasswordAuthentication no
KbdInteractiveAuthentication no
AllowUsers $($settings.user)
AuthorizedKeysFile __PROGRAMDATA__/ssh/administrators_authorized_keys
Subsystem sftp internal-sftp
"@
Set-Content C:\ProgramData\ssh\sshd_config -Value $config -Encoding ascii
Get-NetFirewallRule -Name 'OpenSSH-Server-In-TCP' -ErrorAction SilentlyContinue | Disable-NetFirewallRule
Get-NetFirewallRule -Name 'GunyahVM-SSH' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
New-NetFirewallRule -Name 'GunyahVM-SSH' -DisplayName 'GunyahVM control' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 22 -RemoteAddress $settings.gateway | Out-Null
& (Join-Path $PSScriptRoot 'install-openssh.ps1')
$sshRoot = Join-Path $env:ProgramFiles 'GunyahVM\OpenSSH-ARM64'
& (Join-Path $sshRoot 'ssh-keygen.exe') -A
if ($LASTEXITCODE -ne 0) { throw 'Offline OpenSSH host key generation failed' }
& (Join-Path $sshRoot 'sshd.exe') -t -f C:\ProgramData\ssh\sshd_config
if ($LASTEXITCODE -ne 0) { throw 'Offline OpenSSH configuration validation failed' }
Set-Service sshd -StartupType Automatic
$service = Get-Service sshd
$service.Start()
$service.WaitForStatus('Running', [TimeSpan]::FromSeconds(30))
# RDP remains authenticated with the interactive user's Windows credentials.
# Home editions do not implement an RDP host; keep their SSH control usable.
$edition = (Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion').EditionID
if ($edition -notmatch '^Core') {
    Set-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server' -Name fDenyTSConnections -Value 0
    Set-Service TermService -StartupType Automatic
    Start-Service TermService
    Get-NetFirewallRule -Name 'GunyahVM-RDP' -ErrorAction SilentlyContinue | Remove-NetFirewallRule
    New-NetFirewallRule -Name 'GunyahVM-RDP' -DisplayName 'GunyahVM desktop' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 3389 -RemoteAddress $settings.gateway | Out-Null
}
'GUNYAHVM_AGENT_INSTALLED'
