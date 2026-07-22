"""Runtime behavior tests for the LabTether integration bootstrap."""

from unittest.mock import AsyncMock, MagicMock, call

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

import pytest

import labtether as integration
from labtether.coordinator import LabTetherData
from labtether.const import (
    CONF_ENABLE_RUN_ACTION_SERVICE,
    CONF_IMPORT_BINARY_SENSORS,
    CONF_IMPORT_SENSORS,
    CONF_IMPORT_SWITCHES,
    CONF_SCAN_INTERVAL,
    DEFAULT_ENABLE_RUN_ACTION_SERVICE,
    DEFAULT_SCAN_INTERVAL,
    asset_registry_key,
    hub_registry_key,
)


def _run_action_entry(enabled=True):
    entry = MagicMock()
    entry.options = {CONF_ENABLE_RUN_ACTION_SERVICE: enabled}
    entry.data = {}
    return entry


def test_select_service_target_returns_matching_entry():
    """Service routing should choose the entry that actually owns the asset."""
    coordinator_a = MagicMock()
    coordinator_a.data = LabTetherData(
        assets=[{"id": "a1", "name": "A1", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={},
        firing_alerts_count=0,
    )
    coordinator_b = MagicMock()
    coordinator_b.data = LabTetherData(
        assets=[{"id": "b1", "name": "B1", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={},
        firing_alerts_count=0,
    )
    hass = MagicMock()
    hass.data = {
        "labtether": {
            "entry-a": {"coordinator": coordinator_a},
            "entry-b": {"coordinator": coordinator_b},
        }
    }

    selected = integration._select_service_target(hass, "b1")

    assert selected["coordinator"] is coordinator_b


def test_select_service_target_rejects_ambiguous_asset_without_entry_id():
    """Duplicate cross-hub asset IDs must not route to iteration order."""
    coordinator_a = MagicMock()
    coordinator_a.data = LabTetherData(
        assets=[{"id": "shared", "name": "A", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={},
        firing_alerts_count=0,
    )
    coordinator_b = MagicMock()
    coordinator_b.data = LabTetherData(
        assets=[{"id": "shared", "name": "B", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={},
        firing_alerts_count=0,
    )
    hass = MagicMock()
    hass.data = {
        "labtether": {
            "entry-a": {"coordinator": coordinator_a},
            "entry-b": {"coordinator": coordinator_b},
        }
    }

    with pytest.raises(integration.vol.Invalid, match="provide entry_id"):
        integration._select_service_target(hass, "shared")

    selected = integration._select_service_target(hass, "shared", "entry-b")
    assert selected["coordinator"] is coordinator_b


def test_run_action_service_is_disabled_by_default():
    assert DEFAULT_ENABLE_RUN_ACTION_SERVICE is False


@pytest.mark.asyncio
async def test_run_action_service_is_admin_only_and_bounded_to_power_actions(monkeypatch):
    coordinator = MagicMock()
    coordinator.data = LabTetherData(
        assets=[{"id": "vm-1", "name": "VM", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={},
        firing_alerts_count=0,
    )
    client = MagicMock()
    client.async_run_action = AsyncMock()
    hass = MagicMock()
    hass.services.has_service.return_value = False
    hass.data = {
        "labtether": {
            "entry": {
                "coordinator": coordinator,
                "client": client,
                "entry": _run_action_entry(),
            }
        }
    }
    register_admin_service = MagicMock()
    monkeypatch.setattr(integration, "async_register_admin_service", register_admin_service)

    integration._register_run_action_service(hass)

    register_admin_service.assert_called_once()
    handler = register_admin_service.call_args.args[3]
    allowed_call = MagicMock()
    allowed_call.data = {
        "asset_id": "vm-1",
        "action": "vm.start",
        "connector_id": "proxmox",
    }
    await handler(allowed_call)
    client.async_run_action.assert_awaited_once_with(
        asset_id="vm-1",
        action_type="connector_action",
        connector_id="proxmox",
        action_id="vm.start",
        params=None,
    )

    arbitrary_call = MagicMock()
    arbitrary_call.data = {
        "asset_id": "vm-1",
        "action": "snapshot.delete",
        "connector_id": "proxmox",
        "params": {"force": True},
    }
    with pytest.raises(integration.vol.Invalid, match="not exposed"):
        await handler(arbitrary_call)


@pytest.mark.asyncio
async def test_run_action_service_requires_entry_for_duplicate_asset_ids(monkeypatch):
    """Ambiguous multi-hub actions fail closed and explicit routing is exact."""
    clients = []
    entries = {}
    for entry_id in ("entry-a", "entry-b"):
        coordinator = MagicMock()
        coordinator.data = LabTetherData(
            assets=[{"id": "shared", "name": entry_id, "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
            metrics={},
            firing_alerts_count=0,
        )
        client = MagicMock()
        client.async_run_action = AsyncMock()
        clients.append(client)
        entries[entry_id] = {
            "coordinator": coordinator,
            "client": client,
            "entry": _run_action_entry(),
        }

    hass = MagicMock()
    hass.services.has_service.return_value = False
    hass.data = {"labtether": entries}
    register_admin_service = MagicMock()
    monkeypatch.setattr(integration, "async_register_admin_service", register_admin_service)
    integration._register_run_action_service(hass)
    handler = register_admin_service.call_args.args[3]

    ambiguous_call = MagicMock()
    ambiguous_call.data = {"asset_id": "shared", "action": "vm.start"}
    with pytest.raises(integration.vol.Invalid, match="provide entry_id"):
        await handler(ambiguous_call)
    clients[0].async_run_action.assert_not_awaited()
    clients[1].async_run_action.assert_not_awaited()

    explicit_call = MagicMock()
    explicit_call.data = {
        "asset_id": "shared",
        "action": "vm.start",
        "entry_id": "entry-b",
    }
    await handler(explicit_call)
    clients[0].async_run_action.assert_not_awaited()
    clients[1].async_run_action.assert_awaited_once()


@pytest.mark.asyncio
async def test_run_action_service_does_not_expose_disabled_entry(monkeypatch):
    """One enabled hub must not make disabled hubs mutable through the global service."""
    enabled_coordinator = MagicMock()
    enabled_coordinator.data = LabTetherData(assets=[], metrics={}, firing_alerts_count=0)
    disabled_coordinator = MagicMock()
    disabled_coordinator.data = LabTetherData(
        assets=[{"id": "vm-disabled", "name": "VM", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={},
        firing_alerts_count=0,
    )
    disabled_client = MagicMock()
    disabled_client.async_run_action = AsyncMock()
    hass = MagicMock()
    hass.services.has_service.return_value = False
    hass.data = {
        "labtether": {
            "entry-enabled": {
                "coordinator": enabled_coordinator,
                "client": MagicMock(),
                "entry": _run_action_entry(True),
            },
            "entry-disabled": {
                "coordinator": disabled_coordinator,
                "client": disabled_client,
                "entry": _run_action_entry(False),
            },
        }
    }
    register_admin_service = MagicMock()
    monkeypatch.setattr(integration, "async_register_admin_service", register_admin_service)
    integration._register_run_action_service(hass)
    handler = register_admin_service.call_args.args[3]

    call = MagicMock()
    call.data = {
        "asset_id": "vm-disabled",
        "action": "vm.start",
        "entry_id": "entry-disabled",
    }
    with pytest.raises(integration.vol.Invalid, match="No loaded LabTether entry"):
        await handler(call)
    disabled_client.async_run_action.assert_not_awaited()


def test_legacy_unique_id_migrations_namespace_old_ids():
    """Older global unique ids should migrate to per-entry namespaced ones."""
    coordinator = MagicMock()
    coordinator.entry_id = "entry-1"
    coordinator.data = LabTetherData(
        assets=[{"id": "vm-100", "name": "VM 100", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={},
        firing_alerts_count=0,
    )

    migrations = integration._legacy_unique_id_migrations(coordinator)

    assert migrations[("binary_sensor", "labtether_hub_status")] == "labtether_entry-1_hub_status"
    assert migrations[("sensor", "labtether_vm-100_cpu_used_percent")] == "labtether_entry-1_vm-100_cpu_used_percent"
    assert migrations[("switch", "labtether_vm-100_power")] == "labtether_entry-1_vm-100_power"


def test_migrate_entity_unique_ids_updates_entity_registry():
    """Setup migration should rewrite older entity unique ids before platform setup."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"

    coordinator = MagicMock()
    coordinator.entry_id = "entry-1"
    coordinator.data = LabTetherData(
        assets=[{"id": "vm-100", "name": "VM 100", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={},
        firing_alerts_count=0,
    )

    entity_registry = MagicMock()
    old_entry = MagicMock()
    old_entry.platform = "labtether"
    old_entry.unique_id = "labtether_vm-100_status"
    old_entry.entity_id = "binary_sensor.vm_100_status"
    integration.er.async_get = MagicMock(return_value=entity_registry)
    integration.er.async_entries_for_config_entry = MagicMock(return_value=[old_entry])

    integration._migrate_entity_unique_ids(hass, entry, coordinator)

    entity_registry.async_update_entity.assert_called_once_with(
        "binary_sensor.vm_100_status",
        new_unique_id="labtether_entry-1_vm-100_status",
    )


def test_remove_disabled_entity_registry_entries_removes_only_disabled_domains():
    """Disabling an import category must not leave unavailable registry ghosts."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.data = {}
    entry.options = {
        CONF_IMPORT_BINARY_SENSORS: True,
        CONF_IMPORT_SENSORS: False,
        CONF_IMPORT_SWITCHES: True,
    }

    entity_registry = MagicMock()
    entries = []
    for entity_id, platform in (
        ("sensor.vm_cpu_usage", "labtether"),
        ("sensor.hub_total_assets", "labtether"),
        ("binary_sensor.vm_status", "labtether"),
        ("switch.vm_power", "labtether"),
        ("sensor.foreign", "other_integration"),
    ):
        entity_entry = MagicMock()
        entity_entry.entity_id = entity_id
        entity_entry.platform = platform
        entries.append(entity_entry)

    integration.er.async_get = MagicMock(return_value=entity_registry)
    integration.er.async_entries_for_config_entry = MagicMock(return_value=entries)

    integration._remove_disabled_entity_registry_entries(hass, entry)

    assert entity_registry.async_remove.call_args_list == [
        call("sensor.vm_cpu_usage"),
        call("sensor.hub_total_assets"),
    ]


@pytest.mark.asyncio
async def test_remove_config_entry_device_blocks_hub_device_removal():
    """The synthetic hub device should not be removable."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    device_entry = MagicMock()
    device_entry.identifiers = {("labtether", hub_registry_key("entry-1"))}

    assert await integration.async_remove_config_entry_device(hass, entry, device_entry) is False


@pytest.mark.asyncio
async def test_remove_config_entry_device_allows_stale_asset_removal():
    """Assets not present in current coordinator data should be removable."""
    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    coordinator = MagicMock()
    coordinator.data = LabTetherData(
        assets=[{"id": "a1", "name": "A1", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={},
        firing_alerts_count=0,
    )
    hass.data = {"labtether": {"entry-1": {"coordinator": coordinator}}}

    stale_device = MagicMock()
    stale_device.identifiers = {("labtether", asset_registry_key("entry-1", "missing"))}

    assert await integration.async_remove_config_entry_device(hass, entry, stale_device) is True


@pytest.mark.asyncio
async def test_setup_entry_ensures_hub_device_exists():
    """Setup should create the hub device even when hub entities are disabled."""
    hass = MagicMock()
    hass.data = {}
    hass.services.has_service.return_value = False
    hass.config_entries.async_forward_entry_setups = AsyncMock()
    device_registry = MagicMock()

    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.data = {"host": "https://lab.local:8443", "api_key": "token"}
    entry.options = {}
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock(return_value=lambda: None)

    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()

    integration.dr.async_get = MagicMock(return_value=device_registry)
    integration._build_client = MagicMock(return_value=MagicMock(host="https://lab.local:8443"))
    integration.LabTetherCoordinator = MagicMock(return_value=coordinator)

    result = await integration.async_setup_entry(hass, entry)

    assert result is True
    device_registry.async_get_or_create.assert_called_once()
    assert integration.LabTetherCoordinator.call_args.kwargs["scan_interval_seconds"] == DEFAULT_SCAN_INTERVAL


@pytest.mark.asyncio
async def test_setup_entry_defaults_malformed_stored_scan_interval():
    """Corrupt stored polling intervals should not crash integration setup."""
    hass = MagicMock()
    hass.data = {}
    hass.services.has_service.return_value = False
    hass.config_entries.async_forward_entry_setups = AsyncMock()

    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.data = {"host": "https://lab.local:8443", "api_key": "token"}
    entry.options = {CONF_SCAN_INTERVAL: "30abc"}
    entry.async_on_unload = MagicMock()
    entry.add_update_listener = MagicMock(return_value=lambda: None)

    coordinator = MagicMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()

    integration.dr.async_get = MagicMock(return_value=MagicMock())
    integration._build_client = MagicMock(return_value=MagicMock(host="https://lab.local:8443"))
    integration.LabTetherCoordinator = MagicMock(return_value=coordinator)

    result = await integration.async_setup_entry(hass, entry)

    assert result is True
    assert integration.LabTetherCoordinator.call_args.kwargs["scan_interval_seconds"] == DEFAULT_SCAN_INTERVAL
