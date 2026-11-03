"""The LabTether integration."""

from __future__ import annotations

import logging

from homeassistant import config_entries as ha_config_entries
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.exceptions import ConfigEntryNotReady, HomeAssistantError
import voluptuous as vol

from .api import (
    HubCACertificateError,
    LabTetherApiClient,
    LabTetherApiError,
    LabTetherPermissionError,
)
from .tls import async_load_hub_ca_context
from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_API_KEY,
    CONF_CA_CERTIFICATE,
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


def _build_client(hass: HomeAssistant, entry: ConfigEntry, ssl_context=None) -> LabTetherApiClient:
    session = async_get_clientsession(hass)
    return LabTetherApiClient(
        host=entry.data[CONF_HOST],
        api_key=entry.data[CONF_API_KEY],
        session=session,
        ignore_cert_errors=bool(entry_pref(entry, CONF_IGNORE_CERT_ERRORS, False)),
        allow_insecure_http=bool(
            entry.data.get(CONF_ALLOW_INSECURE_HTTP, DEFAULT_ALLOW_INSECURE_HTTP)
        ),
        ssl_context=ssl_context,
    )


def _ensure_hub_device(
    hass: HomeAssistant, entry: ConfigEntry, configuration_url: str
):
    """Ensure the hub device exists even if hub entities are disabled."""
    device_registry = dr.async_get(hass)
    return device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, hub_registry_key(entry.entry_id))},
        name="LabTether Hub",
        manufacturer="LabTether",
        model="Hub",
        configuration_url=configuration_url,
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
            f"More than one LabTether hub has asset_id {asset_id!r}. Set entry_id to choose the hub."
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


def _reconcile_registry_entries(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: LabTetherCoordinator,
) -> None:
    """Remove stale LabTether entities and devices after a complete inventory poll."""
    entry_id = entry.entry_id
    expected_unique_ids: set[str] = set()
    current_device_keys: set[str] = set()

    if entry_pref(entry, CONF_IMPORT_BINARY_SENSORS, DEFAULT_IMPORT_BINARY_SENSORS):
        expected_unique_ids.add(f"{DOMAIN}_{entry_id}_hub_status")
    if entry_pref(entry, CONF_IMPORT_SENSORS, DEFAULT_IMPORT_SENSORS):
        expected_unique_ids.update(
            {
                f"{DOMAIN}_{entry_id}_hub_total_assets",
                f"{DOMAIN}_{entry_id}_hub_active_alerts",
            }
        )

    for asset in coordinator.data.assets:
        asset_id = asset["id"]
        current_device_keys.add(asset_registry_key(entry_id, asset_id))
        if entry_pref(entry, CONF_IMPORT_BINARY_SENSORS, DEFAULT_IMPORT_BINARY_SENSORS):
            expected_unique_ids.add(f"{DOMAIN}_{entry_id}_{asset_id}_status")
        if (
            entry_pref(entry, CONF_IMPORT_SENSORS, DEFAULT_IMPORT_SENSORS)
            and asset.get("type") in TELEMETRY_KINDS
        ):
            expected_unique_ids.update(
                f"{DOMAIN}_{entry_id}_{asset_id}_{metric_key}"
                for metric_key in (
                    "cpu_used_percent",
                    "memory_used_percent",
                    "disk_used_percent",
                )
            )
        if (
            entry_pref(entry, CONF_IMPORT_SWITCHES, DEFAULT_IMPORT_SWITCHES)
            and asset.get("type") in CONTROLLABLE_KINDS
            and asset.get("source", "") in POWER_ACTION_SOURCES
        ):
            expected_unique_ids.add(f"{DOMAIN}_{entry_id}_{asset_id}_power")

    entity_registry = er.async_get(hass)
    for entity_entry in list(
        er.async_entries_for_config_entry(entity_registry, entry_id)
    ):
        if (
            entity_entry.platform == DOMAIN
            and entity_entry.unique_id not in expected_unique_ids
        ):
            entity_registry.async_remove(entity_entry.entity_id)

    asset_key_prefix = f"{entry_id}:asset:"
    device_registry = dr.async_get(hass)
    for device_entry in list(
        dr.async_entries_for_config_entry(device_registry, entry_id)
    ):
        labtether_asset_keys = {
            identifier
            for identifier_domain, identifier in device_entry.identifiers
            if identifier_domain == DOMAIN and identifier.startswith(asset_key_prefix)
        }
        if labtether_asset_keys and labtether_asset_keys.isdisjoint(current_device_keys):
            device_registry.async_remove_device(device_entry.id)


def _registry_inventory(entry: ConfigEntry, coordinator: LabTetherCoordinator) -> tuple:
    """Only membership, entity kinds and import settings affect stale records."""
    return (
        frozenset(
            (asset["id"], asset.get("type"), asset.get("source", ""))
            for asset in coordinator.data.assets
        ),
        bool(entry_pref(entry, CONF_IMPORT_BINARY_SENSORS, DEFAULT_IMPORT_BINARY_SENSORS)),
        bool(entry_pref(entry, CONF_IMPORT_SENSORS, DEFAULT_IMPORT_SENSORS)),
        bool(entry_pref(entry, CONF_IMPORT_SWITCHES, DEFAULT_IMPORT_SWITCHES)),
    )


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
                f" in entry_id {requested_entry_id!r}"
                if requested_entry_id is not None
                else ""
            )
            raise vol.Invalid(
                f"No LabTether hub with run_action turned on has asset_id {asset_id!r}"
                f"{entry_context}. Check the asset_id, or turn run_action on under Configure."
            )

        asset = target["coordinator"].data.get_asset(asset_id)
        source = str(asset.get("source", "")).strip().lower()
        allowed_actions = RUN_ACTION_ALLOWLIST.get(source, frozenset())
        if action not in allowed_actions:
            allowed = ", ".join(sorted(allowed_actions)) or "none"
            raise vol.Invalid(
                f"Home Assistant can't run {action!r} on this device. Allowed actions: {allowed}."
            )
        if requested_connector_id is not None and requested_connector_id.strip().lower() != source:
            raise vol.Invalid(f"connector_id must be {source!r} for this device, or left out.")
        if params:
            raise vol.Invalid("run_action does not take params. Remove them from the call.")

        client: LabTetherApiClient = target["client"]
        try:
            await client.async_run_action(
                asset_id=asset_id,
                action_type="connector_action",
                connector_id=source,
                action_id=action,
                params=params,
            )
        except LabTetherPermissionError as err:
            raise HomeAssistantError(
                "The LabTether API key can't run actions. Use a key with the actions:exec scope."
            ) from err
        except LabTetherApiError as err:
            raise HomeAssistantError(f"LabTether could not run {action}: {err}") from err
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


def _needs_update_listener() -> bool:
    """Return whether this Home Assistant lacks OptionsFlowWithReload (2025.8).

    Newer versions reload after options, reauth and reconfigure on their own; an
    update listener on top makes them reload twice and log a warning.
    """
    return not hasattr(ha_config_entries, "OptionsFlowWithReload")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up LabTether from a config entry."""
    try:
        ssl_context = await async_load_hub_ca_context(
            hass, entry.data.get(CONF_CA_CERTIFICATE, "")
        )
    except HubCACertificateError as err:
        raise ConfigEntryNotReady("LabTether CA certificate is unavailable") from err
    client = _build_client(hass, entry, ssl_context)
    # The device page's Visit link opens the console; the API origin shows JSON.
    configuration_url = await client.async_get_console_url() or client.host
    hub_device = _ensure_hub_device(hass, entry, configuration_url)
    coordinator = LabTetherCoordinator(
        hass,
        client,
        entry.entry_id,
        scan_interval_seconds=scan_interval_or_default(entry_pref(entry, CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)),
    )
    coordinator.hub_device_id = getattr(hub_device, "id", None)
    coordinator.configuration_url = configuration_url
    await coordinator.async_config_entry_first_refresh()
    _migrate_entity_unique_ids(hass, entry, coordinator)
    _reconcile_registry_entries(hass, entry, coordinator)
    registry_inventory = _registry_inventory(entry, coordinator)

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
        "entry": entry,
    }
    if _needs_update_listener():
        entry.async_on_unload(entry.add_update_listener(_async_reload_entry))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    @callback
    def _async_reconcile_after_refresh() -> None:
        nonlocal registry_inventory
        if not coordinator.last_update_success:
            return
        current_inventory = _registry_inventory(entry, coordinator)
        if current_inventory == registry_inventory:
            return
        _reconcile_registry_entries(hass, entry, coordinator)
        registry_inventory = current_inventory

    entry.async_on_unload(coordinator.async_add_listener(_async_reconcile_after_refresh))
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
