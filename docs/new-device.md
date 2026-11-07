<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Adding a device profile

## Start with capabilities

A device must provide the Gunyah host ABI and matching firmware. Run `gunyahvm check --json` to inspect what the running host exposes. An unknown profile is distinct from a failed capability check: the application still uses conservative defaults. Do not put hardware support tables or kernel-version guesses in frontend/core code.

## Profile data

Add `data/devices/<id>.toml`, following the shipped profiles. Schema 1 contains `id`, `name`, exact DT `compatible` strings, `resources`, `guards`, `sensors`, `display` and explanatory `reasons`. Matching follows DT specificity order; duplicate matches are rejected.

Use only facts from the target device's own DTS and documentation. Resource ceilings and application guard thresholds must be labelled as policy when they are not hardware ratings. Empty sensor-name lists select runtime discovery; nonempty lists restrict reads to those exact power-supply names or thermal-zone types. A known tablet profile requires usable battery and temperature readings before starting.

Profiles cannot provide VMIDs, capability IDs, executable commands, kernel-version switches or feature flags. RM IDs/IRQs come from live DT/FDT; graphics comes from actual host/runtime capabilities. Runtime payload changes follow the [component interface](runtime-components.md).
