"""Tests for LabTether entity identity and device metadata."""

from unittest.mock import MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from labtether.binary_sensor import LabTetherAssetStatusSensor, LabTetherHubStatusSensor
from labtether.coordinator import LabTetherData
from labtether.sensor import LabTetherCpuSensor


def _make_coordinator(entry_id: str, assets, metrics=None):
    coord = MagicMock()
    coord.entry_id = entry_id
    coord.data = LabTetherData(assets=assets, metrics=metrics or {}, firing_alerts_count=0)
    coord.last_update_success = True
    coord.api = MagicMock()
    coord.api.host = "https://lab.local:8443"
    return coord


def test_multi_entry_unique_ids_are_namespaced():
    """Two hubs can expose the same asset id without entity unique-id collisions."""
    assets = [{"id": "vm-100", "name": "VM 100", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}]
    coord_a = _make_coordinator("entry-a", assets, metrics={"vm-100": {"cpu_used_percent": 10.0}})
    coord_b = _make_coordinator("entry-b", assets, metrics={"vm-100": {"cpu_used_percent": 20.0}})

    status_a = LabTetherAssetStatusSensor(coord_a, "vm-100")
    status_b = LabTetherAssetStatusSensor(coord_b, "vm-100")
    cpu_a = LabTetherCpuSensor(coord_a, "vm-100")
    cpu_b = LabTetherCpuSensor(coord_b, "vm-100")
    hub_a = LabTetherHubStatusSensor(coord_a)
    hub_b = LabTetherHubStatusSensor(coord_b)

    assert status_a._attr_unique_id != status_b._attr_unique_id
    assert cpu_a._attr_unique_id != cpu_b._attr_unique_id
    assert hub_a._attr_unique_id != hub_b._attr_unique_id


def test_multi_entry_device_identifiers_are_namespaced():
    """Two hubs can expose the same asset id without device-registry collisions."""
    assets = [{"id": "vm-100", "name": "VM 100", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}]
    coord_a = _make_coordinator("entry-a", assets)
    coord_b = _make_coordinator("entry-b", assets)

    device_info_a = LabTetherAssetStatusSensor(coord_a, "vm-100").device_info
    device_info_b = LabTetherAssetStatusSensor(coord_b, "vm-100").device_info
    hub_info_a = LabTetherHubStatusSensor(coord_a).device_info
    hub_info_b = LabTetherHubStatusSensor(coord_b).device_info

    assert device_info_a["identifiers"] != device_info_b["identifiers"]
    assert device_info_a["via_device"] != device_info_b["via_device"]
    assert hub_info_a["identifiers"] != hub_info_b["identifiers"]


def test_asset_device_links_to_hub_by_registry_id(monkeypatch):
    """Home Assistant 2026.8+ links devices by the hub's registry id; the old
    `via_device` identifier stops working in 2027.8 and devices would lose
    their hub link."""
    import labtether.entity as entity_module

    assets = [{"id": "vm-100", "name": "VM 100", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}]
    coord = _make_coordinator("entry-a", assets)
    coord.hub_device_id = "hub-device-registry-id"

    monkeypatch.setattr(entity_module, "_LINK_BY_DEVICE_ID", True)
    info = LabTetherAssetStatusSensor(coord, "vm-100").device_info
    assert info["via_device_id"] == "hub-device-registry-id"
    assert "via_device" not in info

    monkeypatch.setattr(entity_module, "_LINK_BY_DEVICE_ID", False)
    info = LabTetherAssetStatusSensor(coord, "vm-100").device_info
    assert info["via_device"] == ("labtether", "entry-a:hub")
    assert "via_device_id" not in info


def test_asset_device_shows_agent_version():
    """Hub agents report `metadata.agent_version`; without it every agent
    device in Home Assistant showed no software version."""
    assets = [
        {"id": "srv-1", "name": "nas01", "type": "host", "status": "online",
         "metadata": {"agent_version": "v2.0.0", "version": "old"}},
        {"id": "pve-1", "name": "pve01", "type": "hypervisor", "status": "online",
         "metadata": {"version": "8.4.1"}},
    ]
    coord = _make_coordinator("entry-a", assets)

    assert LabTetherAssetStatusSensor(coord, "srv-1").device_info["sw_version"] == "v2.0.0"
    assert LabTetherAssetStatusSensor(coord, "pve-1").device_info["sw_version"] == "8.4.1"


def test_asset_device_model_is_a_plain_name():
    """The device page showed the Hub's raw kind code (`hypervisor-node`) as
    the model. Known kinds read as plain names, a known connector names its
    own kind, and an unknown kind still reads as words."""
    assets = [
        {"id": "pve-1", "name": "pve01", "type": "hypervisor-node", "source": "proxmox", "status": "online"},
        {"id": "ct-101", "name": "pihole", "type": "container", "source": "proxmox", "status": "online"},
        {"id": "docker-ct-1", "name": "nginx", "type": "docker-container", "source": "docker", "status": "running"},
        {"id": "vm-100", "name": "web01", "type": "vm", "source": "proxmox", "status": "online"},
        {"id": "nas-1", "name": "nas01", "type": "nas", "source": "truenas", "status": "online"},
        {"id": "odd-1", "name": "rack", "type": "rack-shelf", "source": "manual", "status": "online"},
        {"id": "bare-1", "name": "bare", "status": "online"},
    ]
    coord = _make_coordinator("entry-a", assets)

    def model(asset_id):
        return LabTetherAssetStatusSensor(coord, asset_id).device_info["model"]

    assert model("pve-1") == "Proxmox node"
    assert model("ct-101") == "LXC container"
    assert model("docker-ct-1") == "Docker container"
    assert model("vm-100") == "Virtual machine"
    assert model("nas-1") == "NAS"
    assert model("odd-1") == "Rack shelf"
    assert model("bare-1") is None
