"""Keep telemetry polls cheap without leaving removed or disabled entities behind."""

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

import labtether as integration
from labtether.const import CONF_IMPORT_SENSORS
from labtether.coordinator import LabTetherData


@pytest_asyncio.fixture
async def registry_poll(monkeypatch):
    hass = MagicMock()
    hass.data = {}
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    entry = MagicMock(entry_id="entry-1", data={}, options={})
    coordinator = MagicMock(last_update_success=True)
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.data = LabTetherData(assets=[
        {"id": "host-1", "type": "host", "source": "agent", "status": "online"},
        {"id": "vm-1", "type": "vm", "source": "proxmox", "status": "online"},
    ])
    entities = MagicMock()
    devices = MagicMock()
    entity_scan = MagicMock(return_value=[])
    device_scan = MagicMock(return_value=[])
    monkeypatch.setattr(integration, "_build_client", MagicMock())
    monkeypatch.setattr(integration, "_ensure_hub_device", MagicMock())
    monkeypatch.setattr(integration, "_migrate_entity_unique_ids", MagicMock())
    monkeypatch.setattr(integration, "_sync_run_action_service", MagicMock())
    monkeypatch.setattr(integration, "LabTetherCoordinator", MagicMock(return_value=coordinator))
    monkeypatch.setattr(integration.er, "async_get", lambda hass: entities)
    monkeypatch.setattr(integration.er, "async_entries_for_config_entry", entity_scan)
    monkeypatch.setattr(integration.dr, "async_get", lambda hass: devices)
    monkeypatch.setattr(integration.dr, "async_entries_for_config_entry", device_scan)
    await integration.async_setup_entry(hass, entry)
    entity_scan.assert_called_once()
    device_scan.assert_called_once()
    entity_scan.reset_mock()
    device_scan.reset_mock()
    refresh = coordinator.async_add_listener.call_args.args[0]
    return entry, coordinator, refresh, entity_scan, device_scan, entities, devices


@pytest.mark.asyncio
async def test_metric_status_and_order_changes_do_not_rescan_registries(registry_poll):
    _, coordinator, refresh, entity_scan, device_scan, _, _ = registry_poll
    for value in range(10):
        coordinator.data = LabTetherData(
            assets=[
                {"id": "vm-1", "type": "vm", "source": "proxmox", "status": "offline"},
                {"id": "host-1", "type": "host", "source": "agent", "name": "renamed"},
            ],
            metrics={"host-1": {"cpu_used_percent": value}},
            firing_alerts_count=value,
        )
        refresh()
    entity_scan.assert_not_called()
    device_scan.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["remove", "add", "type", "source", "setting"])
async def test_inventory_changes_still_reconcile_stale_records(registry_poll, change):
    entry, coordinator, refresh, entity_scan, device_scan, entities, devices = registry_poll
    assets = [dict(asset) for asset in coordinator.data.assets]
    stale_unique_id = "labtether_entry-1_vm-1_power"
    if change == "remove":
        assets.pop()
    elif change == "add":
        assets.append({"id": "host-2", "type": "host", "source": "agent"})
        stale_unique_id = "labtether_entry-1_missing_status"
    elif change == "type":
        assets[1]["type"] = "network"
    elif change == "source":
        assets[1]["source"] = "unsupported"
    else:
        entry.options[CONF_IMPORT_SENSORS] = False
        stale_unique_id = "labtether_entry-1_host-1_cpu_used_percent"
    entity_scan.return_value = [MagicMock(
        entity_id="sensor.stale", platform="labtether", unique_id=stale_unique_id,
    )]
    device_scan.return_value = [MagicMock(
        id="stale-device", identifiers={("labtether", "entry-1:asset:missing")},
    )]
    coordinator.data = LabTetherData(assets=assets)
    refresh()
    entities.async_remove.assert_called_once_with("sensor.stale")
    devices.async_remove_device.assert_called_once_with("stale-device")
    refresh()
    entity_scan.assert_called_once()
    device_scan.assert_called_once()


@pytest.mark.asyncio
async def test_failed_poll_does_not_remove_records_and_recovery_reconciles(registry_poll):
    _, coordinator, refresh, entity_scan, device_scan, _, _ = registry_poll
    coordinator.last_update_success = False
    coordinator.data = LabTetherData(assets=[])
    refresh()
    entity_scan.assert_not_called()
    device_scan.assert_not_called()
    coordinator.last_update_success = True
    refresh()
    entity_scan.assert_called_once()
    device_scan.assert_called_once()
