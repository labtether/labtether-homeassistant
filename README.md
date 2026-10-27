<div align="center">

<img src=".github/logo.svg" alt="LabTether" width="120" />

# LabTether for Home Assistant

**Bring your homelab servers, VMs and containers into Home Assistant.**

</div>

This integration connects Home Assistant to your
[LabTether](https://github.com/labtether/labtether) hub. Every machine, VM and
container the hub knows about becomes a Home Assistant device with live
sensors. You can start and stop VMs from a dashboard, or let an automation do
it, for example shut down test VMs at night or flash a light when a LabTether
alert fires.

## What you get

- **A device for each machine, VM and container**, with online status and CPU,
  memory and disk sensors.
- **Power switches** to start and stop VMs and containers.
- **Hub sensors** for reachability, device count and active alerts, ready to
  use in automations.
- **The `labtether.run_action` service** for start and stop from your own
  automations. It is off until you turn it on.
- **New devices appear on their own.** No reload needed.
- **More than one hub.** Each connection keeps its own entities, and upgrades
  keep the ones you have.
- **No loops.** It works alongside LabTether's own Home Assistant connector
  without mirroring itself.

You need Home Assistant 2024.12.0 or newer. It is tested with Home Assistant
Core 2026.7.2.

## Install

### HACS (recommended)

1. In HACS, open the menu and choose **Custom repositories**.
2. Add `https://github.com/labtether/labtether-homeassistant` with type
   **Integration** (see the HACS
   [custom repository guide](https://hacs.xyz/docs/faq/custom_repositories/)).
3. Download **LabTether** and restart Home Assistant.

### Manual

Copy `custom_components/labtether` into your Home Assistant
`config/custom_components/` folder and restart Home Assistant.

## Set up

1. Go to **Settings > Devices & Services > Add Integration** and search for
   **LabTether**.
2. Enter your hub URL (for example `https://lab.local:8443`) and an API key
   made for this Home Assistant, with only the access it needs.
3. If your hub uses a private CA, copy its PEM file into the Home Assistant
   config folder first and enter its path, for example
   `/config/labtether-ca.pem`. Certificate and hub-name checks stay on.
4. On **Choose What To Import**, check the live preview of devices and alerts,
   then pick what to create: status entities, telemetry sensors, power
   switches, the `labtether.run_action` service, and how often to poll.
5. Review the summary and finish.

A few safety notes:

- LabTether needs HTTPS except on loopback addresses. **Allow API key over
  insecure HTTP** sends the key unencrypted; turn it on only if you accept that.
- **Ignore TLS certificate errors** is for older setups. Use a CA path instead,
  and never both.

### Change it later

- **Configure** changes what is imported, the polling interval, the service and
  TLS settings.
- If the API key stops working, Home Assistant asks you to sign in again
  instead of making you remove and re-add the hub.
- **Reconfigure** updates the hub URL, CA path or HTTPS setting in place. Keep
  the CA file where it is; Home Assistant retries setup if it goes missing for
  a while.
- When a device is removed from LabTether for good, you can delete its stale
  device in Home Assistant.

## Entities

### Hub device

| Entity | Type | Description |
|--------|------|-------------|
| Status | Binary sensor | Hub reachability |
| Total Assets | Sensor | Count of all monitored devices |
| Active Alerts | Sensor | Count of firing alerts |

### One device per machine, VM or container

| Entity | Type | Description |
|--------|------|-------------|
| Status | Binary sensor | Online or offline |
| CPU Usage | Sensor | CPU use in % (compute devices) |
| Memory Usage | Sensor | Memory use in % (compute devices) |
| Disk Usage | Sensor | Disk use in % (compute devices) |
| Power | Switch | Start and stop (VMs and containers only) |

## The `labtether.run_action` service

Runs an approved start or stop action from an automation you control. It is off
by default and does not expose any other connector actions or parameters.

Use `vm.start` / `vm.stop` for VMs, `ct.start` / `ct.stop` for Proxmox
containers, and `container.start` / `container.stop` for Docker containers.

```yaml
service: labtether.run_action
data:
  asset_id: "pve-vm-100"
  action: "vm.start"
  entry_id: "01JLABTETHERHUBENTRY"
  connector_id: "proxmox"
```

`entry_id` is optional when the asset ID is unique across your LabTether hubs.
If two hubs have the same asset ID, the call is rejected until you pick the
exact config entry.

## Run the hub inside Home Assistant (experimental)

This repository also has a Home Assistant add-on that runs the LabTether hub
itself. For a homelab you rely on, Docker Compose is still the recommended way
to run the hub.

In **Settings > Add-ons > Add-on Store**, add
`https://github.com/labtether/labtether-homeassistant#homeassistant-addon-repo`.
The first-run setup token and database options are in
[`addon/labtether/DOCS.md`](addon/labtether/DOCS.md). The add-on runs the hub
only; install the integration above if you also want LabTether entities.

## Contributing

If you work with an AI coding agent, point it at [AGENTS.md](AGENTS.md) first.
Tests, QA and the release steps are in
[docs/development.md](docs/development.md).

## Help

- [LabTether docs](https://labtether.com/docs) and [GitHub Discussions](https://github.com/labtether/labtether/discussions)
- Security issues: report privately as described in [SECURITY.md](SECURITY.md)

## License

[Apache 2.0](LICENSE). Copyright 2026 LabTether.
