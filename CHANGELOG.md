# Changelog

All notable changes to the LabTether Home Assistant integration and add-on.
Add-on-only notes are also in [`addon/labtether/CHANGELOG.md`](addon/labtether/CHANGELOG.md).

## 1.7.0

Changes since 1.2.0.

### Check before upgrading

- Home Assistant 2024.12.0 or newer is required. The previous `2024.1.0`
  minimum was wrong: the reauthentication, reconfiguration and options flows
  already needed 2024.11 and 2024.12 features.
- The Hub URL must use HTTPS unless it is a loopback address. An existing entry
  that uses plain HTTP to another host stops connecting until you use
  **Reconfigure** to switch to HTTPS, or explicitly enable **Allow API key over
  insecure HTTP**.
- The `labtether.run_action` service is off by default for new setups. Turn it
  on under **Configure** if your automations use it.
- Add-on users: see the add-on changelog for the new `/ssl` mount, port and
  credential file locations.

### Integration

- Trust a private Hub certificate authority by giving the path to its PEM file,
  without turning off certificate checks.
- Power switches and `labtether.run_action` now send the target the Hub
  expects, so actions reach the selected asset. Proxmox LXC containers use the
  `ct.start` and `ct.stop` actions.
- `labtether.run_action` accepts only approved start and stop actions, checks
  that the connector matches the asset, and asks for `entry_id` when the same
  asset ID exists on more than one Hub.
- The active alert count includes every page of alerts. A failed or incomplete
  alert request now fails the refresh instead of showing a partial count.
- Polling fetches assets, metrics and alerts together and publishes one
  complete snapshot. Registries are rescanned only when assets or options
  change.
- Host assets get telemetry sensors. Assets that the Hub imported from Home
  Assistant are skipped so they are not mirrored back.
- Hub responses are checked for size and shape before use, scan intervals must
  be whole seconds between 5 and 3600, and duplicate Hub URLs are detected
  after normalising them.
- Added missing user-facing text for unexpected setup errors.
- Added `codeowners` and an issue tracker link to the integration manifest.

### Add-on

- The Hub runs as an unprivileged user, the add-on mounts only `/ssl`
  read-only, and a one-time setup token creates the owner account.
- Images are built from digest-pinned inputs and are published only after an
  anonymous pull check succeeds.
- A release tag must look like `v1.7.0` and match the integration and add-on
  versions before anything is built.

### Project

- Licensed under the Apache License 2.0. Added a security policy.
- Tests run on Python 3.12 and 3.14. CI enforces a 500-code-line limit per
  handwritten file.

## 1.2.0

First tagged release of the integration and the experimental add-on.
