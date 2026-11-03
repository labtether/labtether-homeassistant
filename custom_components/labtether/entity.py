"""Base entity for LabTether integration."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, asset_registry_key, hub_registry_key
from .coordinator import LabTetherCoordinator

# DeviceInfo lists its keys like a TypedDict, also after it became a dataclass.
_LINK_BY_DEVICE_ID = "via_device_id" in getattr(DeviceInfo, "__optional_keys__", ())


# The Hub's asset kinds (hub internal/model/resource.go and its connectors)
# as the plain names a person reads on the device page.
_KIND_NAMES = {
    "host": "Host",
    "device": "Device",
    "hub": "Hub",
    "hypervisor-node": "Hypervisor node",
    "vm": "Virtual machine",
    "container": "Container",
    "container-host": "Container host",
    "docker-container": "Docker container",
    "connector-cluster": "Connector",
    "nas": "NAS",
    "storage-controller": "Storage controller",
    "storage-pool": "Storage pool",
    "dataset": "Dataset",
    "datastore": "Datastore",
    "disk": "Disk",
    "share-smb": "SMB share",
    "share-nfs": "NFS share",
    "compose-stack": "Compose stack",
    "stack": "Stack",
    "app": "App",
    "service": "Service",
    "ups": "UPS",
    "pdu": "PDU",
}

# The same kind from a known connector, where the source names it better.
_SOURCE_KIND_NAMES = {
    ("proxmox", "hypervisor-node"): "Proxmox node",
    ("proxmox", "container"): "LXC container",
    ("pbs", "storage-controller"): "Proxmox Backup Server",
    ("docker", "container-host"): "Docker host",
}


def _device_model(asset: Mapping[str, Any]) -> str | None:
    """Plain name for the asset's kind; unknown kinds read as words."""
    kind = str(asset.get("type") or "").strip().lower()
    if not kind:
        return None
    source = str(asset.get("source") or "").strip().lower()
    name = _SOURCE_KIND_NAMES.get((source, kind)) or _KIND_NAMES.get(kind)
    if name:
        return name
    words = kind.replace("-", " ").replace("_", " ")
    return words[:1].upper() + words[1:]


def _software_version(asset: Mapping[str, Any]) -> str | None:
    """Hub agents report `agent_version`; `version` is the older key."""
    metadata = asset.get("metadata")
    if not isinstance(metadata, Mapping):
        return None
    for key in ("agent_version", "version"):
        value = metadata.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


class LabTetherEntity(CoordinatorEntity[LabTetherCoordinator]):
    """Base class for LabTether entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LabTetherCoordinator, asset_id: str) -> None:
        super().__init__(coordinator)
        self._asset_id = asset_id

    @property
    def _asset(self) -> Mapping[str, Any] | None:
        """Get the current asset data."""
        return self.coordinator.data.get_asset(self._asset_id)

    @property
    def _metrics(self) -> dict:
        """Get the current metrics for this asset."""
        return self.coordinator.data.get_metrics(self._asset_id)

    @property
    def available(self) -> bool:
        """Return True if the asset exists in the latest data."""
        return self.coordinator.last_update_success and self._asset is not None

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for this asset."""
        asset = self._asset
        entry_id = self.coordinator.entry_id
        if asset is None:
            return DeviceInfo(identifiers={(DOMAIN, asset_registry_key(entry_id, self._asset_id))})

        info = DeviceInfo(
            identifiers={(DOMAIN, asset_registry_key(entry_id, self._asset_id))},
            # An empty name would make Home Assistant name the device after
            # the config entry, so fall back to the Hub's ID for the device.
            name=str(asset.get("name") or "").strip() or self._asset_id,
            manufacturer="LabTether",
            model=_device_model(asset),
            sw_version=_software_version(asset),
        )
        # Link non-hub assets to the hub device. Home Assistant 2026.8 and newer
        # take the hub's registry id; `via_device` stops working in 2027.8.
        if asset.get("type") != "hub":
            hub_device_id = getattr(self.coordinator, "hub_device_id", None)
            if _LINK_BY_DEVICE_ID and isinstance(hub_device_id, str) and hub_device_id:
                info["via_device_id"] = hub_device_id
            else:
                info["via_device"] = (DOMAIN, hub_registry_key(entry_id))
        return info


class LabTetherHubEntity(CoordinatorEntity[LabTetherCoordinator]):
    """Base class for hub-level entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: LabTetherCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device info for the hub."""
        return DeviceInfo(
            identifiers={(DOMAIN, hub_registry_key(self.coordinator.entry_id))},
            name="LabTether Hub",
            manufacturer="LabTether",
            model="Hub",
            configuration_url=self._configuration_url(),
        )

    def _configuration_url(self) -> str:
        """The console address found at setup, else the Hub URL."""
        url = getattr(self.coordinator, "configuration_url", None)
        return url if isinstance(url, str) and url else self.coordinator.api.host
