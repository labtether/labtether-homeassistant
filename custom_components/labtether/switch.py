"""Switch platform for LabTether (VM/container power control)."""

from __future__ import annotations

import logging

from homeassistant.components.switch import SwitchDeviceClass, SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ACTIVE_ASSET_STATUSES,
    DOMAIN,
    CONTROLLABLE_KINDS,
    POWER_ACTION_SOURCES,
    CONF_IMPORT_SWITCHES,
    DEFAULT_IMPORT_SWITCHES,
    entry_pref,
)
from .coordinator import LabTetherCoordinator
from .entity import LabTetherEntity

_LOGGER = logging.getLogger(__name__)
PARALLEL_UPDATES = 1

# Map asset source to connector ID and action naming
_ACTION_MAP = {
    "proxmox": {"start": "vm.start", "stop": "vm.stop", "connector_id": "proxmox"},
    "truenas": {"start": "vm.start", "stop": "vm.stop", "connector_id": "truenas"},
    "docker": {"start": "container.start", "stop": "container.stop", "connector_id": "docker"},
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up LabTether switches."""
    if not entry_pref(entry, CONF_IMPORT_SWITCHES, DEFAULT_IMPORT_SWITCHES):
        return

    coordinator: LabTetherCoordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]

    entities: list[SwitchEntity] = []
    known_asset_ids: set[str] = set()

    def _switchable_asset_ids() -> set[str]:
        return {
            asset["id"]
            for asset in coordinator.data.assets
            if asset.get("type") in CONTROLLABLE_KINDS
            and asset.get("source", "") in POWER_ACTION_SOURCES
            and "id" in asset
        }

    for asset in coordinator.data.assets:
        if asset.get("type") in CONTROLLABLE_KINDS and asset.get("source", "") in POWER_ACTION_SOURCES:
            entities.append(LabTetherPowerSwitch(coordinator, asset["id"]))
            known_asset_ids.add(asset["id"])

    async_add_entities(entities)

    @callback
    def _async_add_new_entities() -> None:
        nonlocal known_asset_ids
        current_asset_ids = _switchable_asset_ids()
        new_asset_ids = sorted(current_asset_ids - known_asset_ids)
        if not new_asset_ids:
            return
        async_add_entities([LabTetherPowerSwitch(coordinator, asset_id) for asset_id in new_asset_ids])
        known_asset_ids |= set(new_asset_ids)

    entry.async_on_unload(coordinator.async_add_listener(_async_add_new_entities))


class LabTetherPowerSwitch(LabTetherEntity, SwitchEntity):
    """Switch to start/stop a VM or container."""

    _attr_device_class = SwitchDeviceClass.SWITCH
    _attr_name = "Power"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator: LabTetherCoordinator, asset_id: str) -> None:
        super().__init__(coordinator, asset_id)
        self._attr_unique_id = f"{DOMAIN}_{coordinator.entry_id}_{asset_id}_power"

    @property
    def is_on(self) -> bool:
        """Return True if the asset is running."""
        asset = self._asset
        if asset is None:
            return False
        status = str(asset.get("status", "")).strip().lower()
        return status in ACTIVE_ASSET_STATUSES

    async def async_turn_on(self, **kwargs) -> None:
        """Start the VM/container."""
        actions = self._power_actions()
        await self.coordinator.api.async_run_action(
            asset_id=self._asset_id,
            action_type="connector_action",
            connector_id=actions["connector_id"],
            action_id=actions["start"],
        )
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        """Stop the VM/container."""
        actions = self._power_actions()
        await self.coordinator.api.async_run_action(
            asset_id=self._asset_id,
            action_type="connector_action",
            connector_id=actions["connector_id"],
            action_id=actions["stop"],
        )
        await self.coordinator.async_request_refresh()

    def _power_actions(self) -> dict[str, str]:
        """Return the current asset action contract or fail visibly."""
        asset = self._asset
        if asset is None:
            raise HomeAssistantError(
                f"LabTether asset {self._asset_id!r} is no longer available"
            )
        source = str(asset.get("source", "")).strip().lower()
        actions = _ACTION_MAP.get(source)
        if actions is None:
            raise HomeAssistantError(
                f"LabTether asset {self._asset_id!r} no longer has a supported power source"
            )
        return actions
