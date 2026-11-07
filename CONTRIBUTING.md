<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->

# Contributing

## Changes

Discuss substantial changes in an issue. Keep Gunyah-specific fixes small, explain user-visible behaviour and add meaningful offline regression tests. Run `python3 tools/lint.py`, `python3 -m pytest -q tools/tests`, and the offline package builder. Update English and Chinese documents together. Do not submit device identifiers or private logs. Device claims require device evidence; a package build alone is insufficient.

## Developer Certificate of Origin

Contributions require the [Developer Certificate of Origin 1.1](https://developercertificate.org/) and your own `Signed-off-by: Name <email>` trailer. Use `git commit -s` only when you can personally certify the DCO. Do not sign on someone else's behalf. Retain upstream authors, copyright notices, licences and existing sign-offs. New project files use `SPDX-License-Identifier: GPL-2.0-only` and their actual copyright holder.

## Third-party changes

Pin the upstream commit and put crosvm changes in the ordered patch queue. Every patch must explain purpose, source and upstream status. Preserve licences specific to the touched component; do not silently relicense imported code. Keep binary source, build inputs and notices available for redistribution. Follow [SECURITY](SECURITY.md) for non-public vulnerability reports.
