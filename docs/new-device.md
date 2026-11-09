<!-- SPDX-License-Identifier: GPL-2.0-only -->
<!-- Copyright (C) 2026 yzddMr6 -->
# Adding a device profile

This guide explains when a new device needs a profile and how to describe its resource and sensor policies without claiming missing kernel support.

## Check the host first

The device must already have matching firmware and a kernel that implements the Gunyah host ABI. Run `gunyahvm load`, then `gunyahvm check --json` to inspect the running host. A profile cannot add a driver, change firmware resource IDs or make unsupported graphics work.

An `UNKNOWN` profile means no device-tree match was found; it is separate from a failed capability check. The application uses conservative defaults for an unknown device. Add a profile when the device needs explicit resource ceilings, sensor selection or display defaults.

## Describe the device

Create `data/devices/<id>.toml` using the shipped profiles as examples. Schema 1 uses `id`, `name`, exact DT `compatible` strings, `resources`, `guards`, `sensors`, `display` and explanatory `reasons`. Matching follows device-tree specificity order; duplicate compatible matches are rejected.

Use the target device's own DTS and documentation for compatible strings and sensor names. Resource ceilings and application guard thresholds are policy, not hardware ratings; explain their basis in `reasons`. Empty sensor lists enable runtime discovery. Nonempty lists restrict reads to those exact power-supply names or thermal-zone types. Known tablet profiles require usable battery and temperature readings before startup.

Profiles cannot contain VMIDs, capability IDs, commands, kernel-version switches or feature flags. Resource-manager IDs and interrupts come from live DT/FDT; graphics availability comes from actual host and runtime capabilities. Changes to binary payloads follow the [runtime component interface](runtime-components.md).

## Check the integration

Run the offline checks after adding the profile:

```sh
python3 tools/lint.py
python3 -m pytest -q tools/tests/test_capabilities.py tools/tests/test_host.py
```

On the target system, check that `gunyahvm check --json` selects the intended profile and reads valid sensors. Then exercise VM creation, startup, guest operation, clean shutdown and resource cleanup with the packaged application. Check graphics on the actual display if documenting graphics support. A matching profile or successful probe alone does not establish VM support.

Document the device's required system release and user-visible capabilities only after checking that combination of firmware, kernel and application. Keep device identity and policy in the profile; do not add board-specific branches to the GUI or VM core.
