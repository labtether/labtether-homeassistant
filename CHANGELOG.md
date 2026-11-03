# Changelog

All notable changes to the LabTether Home Assistant integration and add-on.
Add-on-only notes are also in [`addon/labtether/CHANGELOG.md`](addon/labtether/CHANGELOG.md).

## 2.0.0

Released with LabTether Hub 2.0.0.

### Integration

- The integration uses the same Hub API with Hub 2.0.0 as with 1.7.1. Its
  version now matches the Hub release.
- Home Assistant 2026.11 no longer logs warnings about the integration.
  Reconfigure and reauthentication reload the entry once instead of twice, and
  devices link to the hub device by its registry id on Home Assistant 2026.8
  and newer. The old link stops working in Home Assistant 2027.8.
- The device page shows what each device is as its model, such as **Proxmox
  node**, **LXC container**, **Docker container** or **Virtual machine**. It
  showed the Hub's type code, such as `hypervisor-node`.
- Devices that run a LabTether agent show the agent version as their software
  version. The field was empty: the integration read `version` from the
  device's metadata, and agents report `agent_version`.
- Entity names use the Hub 2.0 words and sentence case. **Total Assets** is now
  **Devices**, and **Active Alerts**, **CPU Usage**, **Memory Usage** and
  **Disk Usage** are now **Active alerts**, **CPU usage**, **Memory usage** and
  **Disk usage**. Names and icons now come from the integration's translation
  and icon files. The Power switch icon shows on and off, and the Active alerts
  icon shows a check mark at 0.
- Upgrades keep your entity IDs, history and automations, because each
  entity's unique ID is unchanged. Only the friendly name changes, unless you
  renamed the entity yourself. New setups get `sensor.labtether_hub_devices`
  where 1.x created `sensor.labtether_hub_total_assets`.
- Setup, options and error text uses plain words and sentence case. Errors from
  power switches and `labtether.run_action` say what to change.
- CPU, memory and disk sensors show one decimal, like the LabTether console.
  They showed every digit the Hub sent, such as `6.11243868695761 %`.
- CPU, memory and disk sensors are unavailable while their device is offline
  or stopped. They kept showing the last values for up to an hour.
- **Visit** on the LabTether Hub device opens the web console the Hub names.
  It opened the Hub API address, which shows only JSON.
- Home Assistant 2026.3 and newer show the LabTether icon for the integration
  and its devices. They showed a placeholder: the icon file was not in the
  `brand` folder those versions read for custom integrations.
- A power switch shows the state you asked for while LabTether carries out the
  action, until the Hub reports the new state or two minutes pass. It flipped
  back to the old state until the next poll, so it looked as if nothing
  happened.
- Errors from power switches and `labtether.run_action` show the Hub's own
  message, such as "Actions are blocked by active maintenance windows". They
  showed "API request failed (HTTP 409)".
- A Hub device without a name, such as the Docker group an agent reports, is
  named by its LabTether ID. Home Assistant named it after the config entry.

### Add-on

- Runs LabTether Hub 2.0.0 with the new 2.0 web console. Ports are unchanged.
- New `external_url` option. The Hub adds its host to its certificate and
  offers it in **Add device**, so agents and browsers on other machines can
  check the Hub's certificate at the Home Assistant host's address.
- A used setup token with no owner account in the database, for example after
  `database_url` points at a new empty database, no longer leaves the Hub
  stopped. The add-on issues a new setup token at start.
- The add-on description and docs use plain words, and the add-on README lists
  the `external_url` option.
- Options have names and descriptions on the add-on's **Configuration** tab
  instead of raw keys such as `labtether_owner_token`.
- With `external_url` set, the Hub names its HTTPS console at that host, so
  the integration's **Visit** link opens it.

### Project

- The add-on container check can run against a candidate Hub image, pinned by
  digest, before that Hub is released.

## 1.7.1

Changes since 1.2.0. 1.7.0 was never released.

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
- Power switches now refuse Home Assistant users who are not administrators.
  Automations still work. Turning a Proxmox switch off now shuts the guest
  down; use `labtether.run_action` with `vm.stop` or `ct.stop` to force it.
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
- Turning a Proxmox power switch off now asks the VM or LXC container to shut
  down instead of force-stopping it. Power switches are limited to Home
  Assistant administrators and automations.
- Docker containers now get power switches and telemetry sensors. The Hub
  reports them as `docker-container`.
- The Hub Status sensor turns off when the Hub stops answering, instead of
  becoming unavailable.
- An API key that lacks a scope now gets its own setup error, and a refused
  power action shows a readable error.
- Asset churn no longer stops polling for good. New asset IDs are limited per
  24 hours, and polling resumes as old IDs age out.
- Added `codeowners` and an issue tracker link to the integration manifest.

### Add-on

- The Hub runs as an unprivileged user, the add-on mounts only `/ssl`
  read-only, and a one-time setup token creates the owner account.
- Images are built from digest-pinned inputs and are published only after an
  anonymous pull check succeeds.
- A release tag must look like `v1.7.1` and match the integration and add-on
  versions before anything is built.
- The base image and Alpine packages are updated to their current security
  releases, including c-ares 1.34.9 and a CA bundle without the withdrawn
  emSign root.

### Project

- Licensed under the Apache License 2.0. Added a security policy.
- Tests run on Python 3.12 and 3.14. CI enforces a 500-code-line limit per
  handwritten file.

## 1.2.0

First tagged release of the integration and the experimental add-on.
