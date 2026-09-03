<div align="center">

<img src=".github/logo.svg" alt="LabTether" width="120" />

</div>

# LabTether Home Assistant Integration

Custom Home Assistant integration that exposes LabTether-managed infrastructure as HA devices and entities.

Compatibility note: audited against Home Assistant Core `2026.7.2`, including
the browser config flow, options, reauthentication, reconfiguration, entity
registry cleanup, outage recovery, restart, and removal behavior.

## Features

- **Monitoring**: Each LabTether asset becomes an HA device with status and telemetry sensors
- **Control**: Start/stop VMs and containers via power switches
- **Automations**: Use the `labtether.run_action` service in HA automations
- **Dynamic discovery**: Newly discovered LabTether assets appear in Home Assistant without reloading the integration
- **Multi-hub safe**: Entity unique IDs and device identifiers are namespaced per LabTether connection
- **Upgrade-safe**: Existing older global entity unique IDs migrate forward to the per-connection scheme during setup
- **Bidirectional**: Works alongside LabTether's built-in HA connector without circular mirroring

## Installation

### HACS (recommended)

Add this repository as a [custom repository](https://hacs.xyz/docs/faq/custom_repositories/) in HACS with category "Integration".

### Development (symlink)

```bash
ln -s /path/to/labtether-homeassistant/custom_components/labtether \
      /path/to/ha-config/custom_components/labtether
```

### Manual

Copy the `custom_components/labtether` directory into your Home Assistant `config/custom_components/` directory.

## Configuration

1. Go to **Settings > Devices & Services > Add Integration**
2. Search for "LabTether"
3. On **Connect to LabTether Hub**, enter:
   - your LabTether hub URL (for example `https://lab.local:8443`),
   - a dedicated least-privilege API key for this Home Assistant instance,
   - an optional display name.
4. Enable **Ignore TLS certificate errors** only when you intentionally use a self-signed LabTether hub certificate and trust that endpoint.
5. LabTether requires HTTPS except for loopback addresses. Enable **Allow API key over insecure HTTP** only when you explicitly accept that the bearer credential will cross the network without transport encryption.
6. Continue to **Choose What To Import** and review the live preview:
   - asset count,
   - telemetry-capable assets,
   - controllable assets,
   - active alerts,
   - visible source summary.
7. Choose what Home Assistant should create:
   - status entities,
   - telemetry sensors,
   - power switches,
   - `labtether.run_action` service,
   - polling interval.
8. Review the summary and finish setup.

## Options Flow

After setup, open the LabTether integration entry and use **Configure** to update:

- TLS ignore-certificate behavior,
- imported entity categories,
- `labtether.run_action` service availability,
- polling interval.

If the API key becomes invalid, Home Assistant can now drive a reauthentication flow for the LabTether entry instead of forcing remove/re-add.

If the hub URL, HTTPS policy, or other required connection details change, use **Reconfigure** on the integration entry to update the connection in place.

If a LabTether asset is removed permanently, Home Assistant can now remove its stale device entry cleanly from the device registry.

## Entities

### Hub Device
| Entity | Type | Description |
|--------|------|-------------|
| Status | Binary Sensor | Hub reachability |
| Total Assets | Sensor | Count of all monitored assets |
| Active Alerts | Sensor | Count of firing alerts |

### Per-Asset Devices
| Entity | Type | Description |
|--------|------|-------------|
| Status | Binary Sensor | Online/offline status |
| CPU Usage | Sensor | CPU utilization % (compute assets) |
| Memory Usage | Sensor | Memory utilization % (compute assets) |
| Disk Usage | Sensor | Disk utilization % (compute assets) |
| Power | Switch | Start/stop (VMs and containers only) |

## Services

### `labtether.run_action`

Run an approved start/stop action from an administrator-owned automation. This
service is disabled by default and intentionally does not expose arbitrary
connector actions or parameters.

```yaml
service: labtether.run_action
data:
  asset_id: "pve-vm-100"
  action: "vm.start"
  entry_id: "01JLABTETHERHUBENTRY"
  connector_id: "proxmox"
```

`entry_id` is optional when the asset ID is unique across loaded LabTether
hubs. If multiple hubs expose the same asset ID, the service rejects the call
until the exact Home Assistant config entry is selected.

## Development

```bash
python -m pip install --require-hashes -r requirements-dev.txt
python -m pytest tests/ -v
./tests/addon_container_security_test.sh  # requires Docker
LABTETHER_LIVE_HA_QA=1 ./tests/ha_core_live_test.sh  # disposable HA Core lifecycle
./tests/ha_core_cross_product_instance.sh start     # disposable hub-connector target
```

The cross-product instance preserves its HTTP route and also creates an HTTPS
route for connector TLS testing. It writes its seven-day QA token to
`/tmp/labtether-ha-cross-qa-token` and its disposable CA certificate to
`/tmp/labtether-ha-cross-tls/ca.pem`, both with mode `0600`. Docker-hosted hubs
can use `https://host.docker.internal:18444`; untrusted TLS fails closed, while
the connector's explicit `skip_verify` option can be exercised for this isolated
self-signed target. Use `status`, `restart`, or `stop` to manage the whole
instance, or `tls-start`, `tls-restart`, and `tls-stop` to manage only the HTTPS
route. Never reuse its disposable credentials or TLS bypass outside QA.
Each TLS restart generates a fresh certificate chain, so CA-trusting clients
must reread the PEM from the same path afterward.

To verify the exact candidate hub connector path without printing either token:

```bash
LABTETHER_QA_HUB_CONTAINER=<hub-container> \
LABTETHER_QA_HUB_URL=https://<hub-host>:<port> \
LABTETHER_QA_HUB_CA_FILE=</path/to/candidate-hub-ca.pem> \
  ./tests/verify_ha_cross_tls_connector.sh
```

Omit `LABTETHER_QA_HUB_CA_FILE` only when the candidate Hub certificate is
already trusted by the system. The verifier never disables candidate Hub
certificate or hostname checks.
