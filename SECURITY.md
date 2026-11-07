<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# Security policy

## Reporting

Send vulnerability reports to yzddMr6 at **yzddmr6@gmail.com**. Include affected version, threat model, minimal reproduction and expected impact. Do not open a public issue with exploit details before coordinated disclosure; do not send passwords, private keys, ISO contents or full VM disks. The maintainer will discuss remediation and disclosure timing with the reporter; no response-time guarantee is made.

## Scope

The current 1.0 series receives security fixes when available. Reports cover the management commands, privilege helpers, packaging and carried patches. Upstream crosvm, minijail, edk2 and driver vulnerabilities may require coordinated upstream fixes. Read [the security model](docs/security.md), especially local UID sharing, finite per-process memory limits, test-signed Windows drivers and loopback VNC access.
