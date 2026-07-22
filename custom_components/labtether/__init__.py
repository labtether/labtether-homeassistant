"""The LabTether integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service import async_register_admin_service
import voluptuous as vol

from .api import LabTetherApiClient
from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_API_KEY,
    CONF_ALLOW_INSECURE_HTTP,
    CONF_IGNORE_CERT_ERRORS,
    CONF_ENABLE_RUN_ACTION_SERVICE,
    CONF_IMPORT_BINARY_SENSORS,
    CONF_IMPORT_SENSORS,
    CONF_IMPORT_SWITCHES,
    CONF_SCAN_INTERVAL,
    DEFAULT_ENABLE_RUN_ACTION_SERVICE,
    DEFAULT_ALLOW_INSECURE_HTTP,
    DEFAULT_IMPORT_BINARY_SENSORS,
    DEFAULT_IMPORT_SENSORS,
    DEFAULT_IMPORT_SWITCHES,
    DEFAULT_SCAN_INTERVAL,
    PLATFORMS,
    entry_pref,
    scan_interval_or_default,
    TELEMETRY_KINDS,
    CONTROLLABLE_KINDS,
    POWER_ACTION_SOURCES,
    RUN_ACTION_ALLOWLIST,
    hub_registry_key,
    asset_registry_key,
)
from .coordinator import LabTetherCoordinator

_LOGGER = logging.getLogger(__name__)

SERVICE_RUN_ACTION = "run_action"
SERVICE_RUN_ACTION_SCHEMA = vol.Schema(
    {
        vol.Required("asset_id"): cv.string,
        vol.Required("action"): cv.string,
        vol.Optional("entry_id"): cv.string,
        vol.Optional("connector_id"): cv.string,
        vol.Optional("params"): dict,
    }
)


def _run_action_enabled(entry: ConfigEntry) -> bool:
    return bool(entry_pref(entry, CONF_ENABLE_RUN_ACTION_SERVICE, DEFAULT_ENABLE_RUN_ACTION_SERVICE))


def _build_client(hass: HomeAssistant, entry: ConfigEntry) -> LabTetherApiClient:
    session = async_get_clientsession(hass)
    return LabTetherApiClient(
        host=entry.data[CONF_HOST],
        api_key=entry.data[CONF_API_KEY],
        session=session,
        ignore_cert_errors=bool(entry_pref(entry, CONF_IGNORE_CERT_ERRORS, False)),
        allow_insecure_http=bool(
            entry.data.get(CONF_ALLOW_INSECURE_HTTP, DEFAULT_ALLOW_INSECURE_HTTP)
        ),
    )


def _ensure_hub_device(hass: HomeAssistant, entry: ConfigEntry, client: LabTetherApiClient) -> None:
    """Ensure the hub device exists even if hub entities are disabled."""
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, hub_registry_key(entry.entry_id))},
        name="LabTether Hub",
        manufacturer="LabTether",
        model="Hub",
        configuration_url=client.host,
    )


def _select_service_target(
    hass: HomeAssistant,
    asset_id: str,
    entry_id: str | None = None,
    *,
    require_run_action_enabled: bool = False,
):
    """Find exactly one loaded entry that owns the requested asset."""
    entries = hass.data.get(DOMAIN, {})

    def eligible(entry_data) -> bool:
        if not require_run_action_enabled:
            return True
        entry = entry_data.get("entry")
        return entry is not None and _run_action_enabled(entry)

    requested_entry_id = str(entry_id or "").strip()
    if requested_entry_id:
        entry_data = entries.get(requested_entry_id)
        if entry_data is None or not eligible(entry_data):
            return None
        coordinator = entry_data["coordinator"]
        if coordinator.data.get_asset(asset_id) is None:
            return None
        return entry_data

    matches = [
        entry_data
        for entry_data in entries.values()
        if eligible(entry_data)
        and entry_data["coordinator"].data.get_asset(asset_id) is not None
    ]
    if len(matches) > 1:
        raise vol.Invalid(
            f"Multiple LabTether entries expose asset_id={asset_id!r}; provide entry_id"
        )
    return matches[0] if matches else None


def _legacy_unique_id_migrations(coordinator: LabTetherCoordinator) -> dict[tuple[str, str], str]:
    """Build entity-registry unique-id migrations for older global IDs."""
    entry_id = coordinator.entry_id
    migrations = {
        ("binary_sensor", f"{DOMAIN}_hub_status"): f"{DOMAIN}_{entry_id}_hub_status",
        ("sensor", f"{DOMAIN}_hub_total_assets"): f"{DOMAIN}_{entry_id}_hub_total_assets",
        ("sensor", f"{DOMAIN}_hub_active_alerts"): f"{DOMAIN}_{entry_id}_hub_active_alerts",
    }

    for asset in coordinator.data.assets:
        asset_id = asset.get("id")
        if not asset_id:
            continue

        migrations[("binary_sensor", f"{DOMAIN}_{asset_id}_status")] = (
            f"{DOMAIN}_{entry_id}_{asset_id}_status"
        )

        if asset.get("type") in TELEMETRY_KINDS:
            for metric_key in ("cpu_used_percent", "memory_used_percent", "disk_used_percent"):
                migrations[("sensor", f"{DOMAIN}_{asset_id}_{metric_key}")] = (
                    f"{DOMAIN}_{entry_id}_{asset_id}_{metric_key}"
                )

        if (
            asset.get("type") in CONTROLLABLE_KINDS
            and asset.get("source", "") in POWER_ACTION_SOURCES
        ):
            migrations[("switch", f"{DOMAIN}_{asset_id}_power")] = (
                f"{DOMAIN}_{entry_id}_{asset_id}_power"
            )

    return migrations


def _migrate_entity_unique_ids(hass: HomeAssistant, entry: ConfigEntry, coordinator: LabTetherCoordinator) -> None:
    """Migrate older global entity unique IDs to per-entry namespaced IDs."""
    entity_registry = er.async_get(hass)
    migrations = _legacy_unique_id_migrations(coordinator)
    for entity_entry in er.async_entries_for_config_entry(entity_registry, entry.entry_id):
        entity_domain = entity_entry.entity_id.partition(".")[0]
        new_unique_id = migrations.get((entity_domain, entity_entry.unique_id))
        if new_unique_id and new_unique_id != entity_entry.unique_id:
            entity_registry.async_update_entity(
                entity_entry.entity_id, new_unique_id=new_unique_id
            )


def _remove_disabled_entity_registry_entries(
    hass: HomeAssistant, entry: ConfigEntry
) -> None:
    """Remove registry ghosts for entity categories the operator disabled."""
    preferences = {
        "binary_sensor": entry_pref(
            entry, CONF_IMPORT_BINARY_SENSORS, DEFAULT_IMPORT_BINARY_SENSORS
        ),
        "sensor": entry_pref(entry, CONF_IMPORT_SENSORS, DEFAULT_IMPORT_SENSORS),
        "switch": entry_pref(
            entry, CONF_IMPORT_SWITCHES, DEFAULT_IMPORT_SWITCHES
        ),
    }
    disabled_domains = {
        entity_domain
        for entity_domain, enabled in preferences.items()
        if not bool(enabled)
    }
    if not disabled_domains:
        return

    entity_registry = er.async_get(hass)
    for entity_entry in list(
        er.async_entries_for_config_entry(entity_registry, entry.entry_id)
    ):
        entity_domain = entity_entry.entity_id.partition(".")[0]
        if entity_entry.platform == DOMAIN and entity_domain in disabled_domains:
            entity_registry.async_remove(entity_entry.entity_id)


def _register_run_action_service(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_RUN_ACTION):
        return

    async def handle_run_action(call: ServiceCall) -> None:
        """Handle the run_action service call."""
        asset_id = call.data["asset_id"]
        action = call.data["action"].strip()
        requested_entry_id = call.data.get("entry_id")
        requested_connector_id = call.data.get("connector_id")
        params = call.data.get("params")

        target = _select_service_target(
            hass,
            asset_id,
            requested_entry_id,
            require_run_action_enabled=True,
        )
        if target is None:
            entry_context = (
                f" in entry_id={requested_entry_id!r}"
                if requested_entry_id is not None
                else ""
            )
            raise vol.Invalid(
                f"No loaded LabTether entry exposes asset_id={asset_id!r}{entry_context}"
            )

        asset = target["coordinator"].data.get_asset(asset_id)
        source = str(asset.get("source", "")).strip().lower()
        allowed_actions = RUN_ACTION_ALLOWLIST.get(source, frozenset())
        if action not in allowed_actions:
            raise vol.Invalid(
                f"Action {action!r} is not exposed through Home Assistant for asset source {source!r}"
            )
        if requested_connector_id is not None and requested_connector_id.strip().lower() != source:
            raise vol.Invalid("connector_id must match the selected asset source")
        if params:
            raise vol.Invalid("run_action does not accept arbitrary parameters")

        client: LabTetherApiClient = target["client"]
        await client.async_run_action(
            asset_id=asset_id,
            action_type="connector_action",
            connector_id=source,
            action_id=action,
            params=params,
        )
    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_RUN_ACTION,
        handle_run_action,
        schema=SERVICE_RUN_ACTION_SCHEMA,
    )


def _sync_run_action_service(hass: HomeAssistant) -> None:
    entries = hass.data.get(DOMAIN, {})
    if any(_run_action_enabled(entry_data["entry"]) for entry_data in entries.values()):
        _register_run_action_service(hass)
    elif hass.services.has_service(DOMAIN, SERVICE_RUN_ACTION):
        hass.services.async_remove(DOMAIN, SERVICE_RUN_ACTION)


async def _async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry after options change."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up LabTether from a config entry."""
    client = _build_client(hass, entry)
    _ensure_hub_device(hass, entry, client)
    coordinator = LabTetherCoordinator(
        hass,
        client,
        entry.entry_id,
        scan_interval_seconds=scan_interval_or_default(entry_pref(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
    )
    await coordinator.async_config_entry_first_refresh()
    _migrate_entity_unique_ids(hass, entry, coordinator)
    _remove_disabled_entity_registry_entries(hass, entry)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
        "entry": entry,
    }
    entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    _sync_run_action_service(hass)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a LabTether config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
        _sync_run_action_service(hass)
    return unload_ok


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: ConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow manual removal of stale asset devices from the registry."""
    if (DOMAIN, hub_registry_key(entry.entry_id)) in device_entry.identifiers:
        return False

    current_assets = hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("coordinator")
    current_asset_ids = set()
    if current_assets is not None:
        current_asset_ids = {
            asset_registry_key(entry.entry_id, asset["id"])
            for asset in current_assets.data.assets
            if "id" in asset
        }

    return not any(
        identifier_domain == DOMAIN and identifier_id in current_asset_ids
        for identifier_domain, identifier_id in device_entry.identifiers
    )
