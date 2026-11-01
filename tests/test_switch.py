"""Tests for LabTether switch entities."""

import pytest
from dataclasses import replace
from unittest.mock import MagicMock, AsyncMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from labtether.api import LabTetherPermissionError
from labtether.switch import LabTetherPowerSwitch, async_setup_entry
from labtether.coordinator import LabTetherData
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, Unauthorized


def _make_coordinator(assets, metrics=None):
    coord = MagicMock()
    coord.entry_id = "entry-1"
    coord.data = LabTetherData(assets=assets, metrics=metrics or {}, firing_alerts_count=0)
    coord.last_update_success = True
    coord.api = MagicMock()
    coord.api.host = "http://192.168.1.10:8080"
    coord.api.async_run_action = AsyncMock()
    coord.async_request_refresh = AsyncMock()
    return coord


def test_power_switch_is_on_when_online():
    """Switch should be on when asset status is online/running."""
    coord = _make_coordinator([{"id": "vm-1", "name": "TestVM", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}])
    switch = LabTetherPowerSwitch(coord, "vm-1")
    assert switch.is_on is True


@pytest.mark.parametrize("status", ["running", "RUNNING", "up", "active", "healthy"])
def test_power_switch_active_runtime_statuses_are_on(status):
    """Power state should match the normalized active connectivity states."""
    coord = _make_coordinator([
        {"id": "vm-1", "name": "TestVM", "type": "vm", "source": "proxmox", "status": status, "metadata": {}}
    ])
    switch = LabTetherPowerSwitch(coord, "vm-1")
    assert switch.is_on is True


def test_power_switch_is_off_when_offline():
    """Switch should be off when asset status is offline/stopped."""
    coord = _make_coordinator([{"id": "vm-1", "name": "TestVM", "type": "vm", "source": "proxmox", "status": "offline", "metadata": {}}])
    switch = LabTetherPowerSwitch(coord, "vm-1")
    assert switch.is_on is False


@pytest.mark.asyncio
async def test_turn_on_calls_start_action():
    """Turning on should call vm.start action."""
    coord = _make_coordinator([{"id": "vm-1", "name": "TestVM", "type": "vm", "source": "proxmox", "status": "offline", "metadata": {}}])
    switch = LabTetherPowerSwitch(coord, "vm-1")
    await switch.async_turn_on()
    coord.api.async_run_action.assert_called_once_with(
        asset_id="vm-1",
        action_type="connector_action",
        connector_id="proxmox",
        action_id="vm.start",
    )


@pytest.mark.asyncio
async def test_turn_off_asks_proxmox_vm_to_shut_down():
    """Turning off must not force-stop a Proxmox VM (vm.stop is a force stop)."""
    coord = _make_coordinator([{"id": "vm-1", "name": "TestVM", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}])
    switch = LabTetherPowerSwitch(coord, "vm-1")
    await switch.async_turn_off()
    coord.api.async_run_action.assert_called_once_with(
        asset_id="vm-1",
        action_type="connector_action",
        connector_id="proxmox",
        action_id="vm.shutdown",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("verb", "action"), [("start", "container.start"), ("stop", "container.stop")])
async def test_docker_container_from_hub_gets_power_control(verb, action):
    """The Hub reports Docker containers as docker-container, not container."""
    asset = {"id": "docker-ct-1", "type": "docker-container", "source": "docker", "status": "running"}
    coord = _make_coordinator([asset])
    added = []
    hass = MagicMock()
    hass.data = {"labtether": {"entry-1": {"coordinator": coord}}}
    entry = MagicMock(entry_id="entry-1", options={}, data={})
    await async_setup_entry(hass, entry, lambda entities: added.extend(entities))

    assert [switch._asset_id for switch in added] == ["docker-ct-1"]
    await (added[0].async_turn_on() if verb == "start" else added[0].async_turn_off())
    coord.api.async_run_action.assert_awaited_once_with(
        asset_id="docker-ct-1", action_type="connector_action", connector_id="docker",
        action_id=action,
    )


@pytest.mark.asyncio
async def test_power_control_is_limited_to_administrators():
    """A non-admin dashboard user must not power assets on or off."""
    coord = _make_coordinator([{"id": "vm-1", "type": "vm", "source": "proxmox", "status": "online"}])
    switch = LabTetherPowerSwitch(coord, "vm-1")
    switch.hass = MagicMock()
    switch.hass.auth.async_get_user = AsyncMock(return_value=MagicMock(is_admin=False))
    switch.async_set_context(Context(user_id="user-1"))

    with pytest.raises(Unauthorized):
        await switch.async_turn_off()
    coord.api.async_run_action.assert_not_awaited()

    switch.hass.auth.async_get_user.return_value = MagicMock(is_admin=True)
    await switch.async_turn_off()
    coord.api.async_run_action.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_action_scope_is_a_readable_error():
    """A Hub refusal must reach the user as a Home Assistant error, not a traceback."""
    coord = _make_coordinator([{"id": "vm-1", "type": "vm", "source": "proxmox", "status": "offline"}])
    coord.api.async_run_action.side_effect = LabTetherPermissionError("refused")
    switch = LabTetherPowerSwitch(coord, "vm-1")

    with pytest.raises(HomeAssistantError, match="actions:exec"):
        await switch.async_turn_on()
    coord.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("verb", ["start", "stop"])
async def test_proxmox_container_uses_lxc_power_action(verb):
    """LXC power must reach the Hub's CT route, never its QEMU VM route."""
    coord = _make_coordinator([
        {"id": "ct-1", "type": "container", "source": "proxmox", "status": "offline"}
    ])
    switch = LabTetherPowerSwitch(coord, "ct-1")

    if verb == "start":
        await switch.async_turn_on()
    else:
        await switch.async_turn_off()

    coord.api.async_run_action.assert_awaited_once_with(
        asset_id="ct-1", action_type="connector_action", connector_id="proxmox",
        action_id="ct.start" if verb == "start" else "ct.shutdown",
    )
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_power_switch_fails_instead_of_reporting_success():
    """A removed asset must not make a switch mutation look successful."""
    coord = _make_coordinator([])
    switch = LabTetherPowerSwitch(coord, "missing")

    with pytest.raises(HomeAssistantError, match="no longer available"):
        await switch.async_turn_on()

    coord.api.async_run_action.assert_not_awaited()
    coord.async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_switch_setup_skips_unsupported_sources_and_adds_new_supported_assets():
    """Only supported sources should get power switches, including newly discovered ones."""
    coord = _make_coordinator([
        {"id": "vm-1", "name": "VM1", "type": "vm", "source": "unknown", "status": "offline", "metadata": {}}
    ])
    listeners = []
    coord.async_add_listener = MagicMock(side_effect=lambda cb: listeners.append(cb) or (lambda: None))

    hass = MagicMock()
    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.options = {}
    entry.data = {}
    entry.async_on_unload = MagicMock()
    hass.data = {"labtether": {"entry-1": {"coordinator": coord}}}

    added_batches = []

    def _add_entities(entities):
        added_batches.append(list(entities))

    await async_setup_entry(hass, entry, _add_entities)
    assert added_batches[0] == []

    new_asset = {"id": "vm-2", "name": "VM2", "type": "vm", "source": "proxmox", "status": "offline", "metadata": {}}
    assets = [*coord.data.assets, new_asset]
    coord.data = replace(coord.data, assets=assets)
    listeners[0]()

    assert len(added_batches[1]) == 1
    assert added_batches[1][0]._asset_id == "vm-2"
    assert added_batches[1][0]._attr_unique_id == "labtether_entry-1_vm-2_power"

    for source in ("unknown", "proxmox"):
        new_asset["source"] = source
        coord.data = replace(coord.data, assets=assets)
        listeners[0]()

    assert len(added_batches) == 3
    assert added_batches[2][0]._asset_id == "vm-2"
