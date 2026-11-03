"""Tests for LabTether sensor entities."""

import pytest
from dataclasses import replace
from unittest.mock import MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from labtether.sensor import (
    LabTetherCpuSensor,
    LabTetherMemorySensor,
    LabTetherDiskSensor,
    LabTetherTotalAssetsSensor,
    LabTetherActiveAlertsSensor,
    async_setup_entry,
)
from labtether.coordinator import LabTetherData


def _make_coordinator(assets, metrics=None, alerts=0):
    coord = MagicMock()
    coord.entry_id = "entry-1"
    coord.data = LabTetherData(assets=assets, metrics=metrics or {}, firing_alerts_count=alerts)
    coord.last_update_success = True
    coord.api = MagicMock()
    coord.api._host = "http://192.168.1.10:8080"
    return coord


def test_cpu_sensor_value():
    """CPU sensor should return cpu_used_percent from metrics."""
    coord = _make_coordinator(
        [{"id": "a1", "name": "Node1", "type": "hypervisor-node", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={"a1": {"cpu_used_percent": 45.5}},
    )
    sensor = LabTetherCpuSensor(coord, "a1")
    assert sensor.native_value == 45.5


def test_memory_sensor_value():
    """Memory sensor should return memory_used_percent."""
    coord = _make_coordinator(
        [{"id": "a1", "name": "Node1", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={"a1": {"memory_used_percent": 72.0}},
    )
    sensor = LabTetherMemorySensor(coord, "a1")
    assert sensor.native_value == 72.0


def test_disk_sensor_value():
    """Disk sensor should return disk_used_percent."""
    coord = _make_coordinator(
        [{"id": "a1", "name": "Node1", "type": "hypervisor-node", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={"a1": {"disk_used_percent": 30.0}},
    )
    sensor = LabTetherDiskSensor(coord, "a1")
    assert sensor.native_value == 30.0


def test_metric_sensors_use_percentage_without_device_class():
    """Utilization sensors should expose % units without a semantic device class."""
    coord = _make_coordinator(
        [{"id": "a1", "name": "Node1", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={"a1": {"cpu_used_percent": 10.0, "memory_used_percent": 20.0, "disk_used_percent": 30.0}},
    )
    cpu = LabTetherCpuSensor(coord, "a1")
    memory = LabTetherMemorySensor(coord, "a1")
    disk = LabTetherDiskSensor(coord, "a1")
    for sensor in (cpu, memory, disk):
        assert sensor._attr_native_unit_of_measurement == "%"
        assert getattr(sensor, "_attr_device_class", None) is None
        # The Hub sends values like 6.11243868695761; Home Assistant shows every
        # digit unless the sensor suggests a precision.
        assert sensor._attr_suggested_display_precision == 1


def test_sensor_returns_none_when_no_metrics():
    """Sensor should return None when metrics are unavailable."""
    coord = _make_coordinator(
        [{"id": "a1", "name": "Node1", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={},
    )
    sensor = LabTetherCpuSensor(coord, "a1")
    assert sensor.native_value is None


def test_total_assets_sensor():
    """Total assets sensor should count all assets."""
    coord = _make_coordinator([
        {"id": "a1", "name": "A1", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}},
        {"id": "a2", "name": "A2", "type": "vm", "source": "proxmox", "status": "offline", "metadata": {}},
    ])
    sensor = LabTetherTotalAssetsSensor(coord)
    assert sensor.native_value == 2


def test_active_alerts_sensor():
    """Active alerts sensor should return firing count."""
    coord = _make_coordinator([], alerts=5)
    sensor = LabTetherActiveAlertsSensor(coord)
    assert sensor.native_value == 5


@pytest.mark.asyncio
async def test_sensor_setup_adds_new_telemetry_assets_after_initial_load():
    """Agent hosts and later telemetry assets should receive metric entities."""
    coord = _make_coordinator([
        {"id": "a1", "name": "Agent1", "type": "host", "source": "agent", "status": "online", "metadata": {}}
    ], metrics={"a1": {"cpu_used_percent": 45.5}})
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
    assert len(added_batches[0]) == 5  # 2 hub sensors + 3 metrics for first asset
    assert added_batches[0][0]._attr_unique_id == "labtether_entry-1_hub_total_assets"
    assert {entity._metric_key for entity in added_batches[0][2:]} == {
        "cpu_used_percent", "memory_used_percent", "disk_used_percent"
    }
    assert added_batches[0][2].native_value == 45.5

    new_asset = {"id": "a2", "name": "VM2", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}
    assets = [*coord.data.assets, new_asset]
    coord.data = replace(coord.data, assets=assets)
    listeners[0]()

    assert len(added_batches[1]) == 3
    assert {entity._asset_id for entity in added_batches[1]} == {"a2"}

    for asset_type in ("network-device", "vm"):
        new_asset["type"] = asset_type
        coord.data = replace(coord.data, assets=assets)
        listeners[0]()

    assert len(added_batches) == 3
    assert {entity._asset_id for entity in added_batches[2]} == {"a2"}


@pytest.mark.parametrize(("status", "available"), [("online", True), ("running", True), ("offline", False), ("stopped", False)])
def test_metric_sensors_of_an_offline_device_are_unavailable(status, available):
    """The Hub keeps returning a device's last CPU, memory and disk values for
    an hour after it goes offline; Home Assistant must not show them as live."""
    coord = _make_coordinator(
        [{"id": "a1", "name": "Node1", "type": "host", "source": "agent", "status": status, "metadata": {}}],
        metrics={"a1": {"cpu_used_percent": 27.6, "memory_used_percent": 54.6, "disk_used_percent": 86.9}},
    )
    for sensor in (LabTetherCpuSensor(coord, "a1"), LabTetherMemorySensor(coord, "a1"), LabTetherDiskSensor(coord, "a1")):
        assert sensor.available is available


@pytest.mark.asyncio
async def test_usage_sensors_only_for_metrics_the_hub_sends():
    """A Docker host's three sensors and a Docker container's disk sensor
    stayed unknown forever: the Hub sends no such values for them."""
    coord = _make_coordinator([
        {"id": "h1", "type": "host", "source": "agent", "status": "online"},
        {"id": "docker-host-h1", "type": "container-host", "source": "docker", "status": "online"},
        {"id": "docker-ct-h1-abc", "type": "docker-container", "source": "docker", "status": "online"},
    ])
    coord.async_add_listener = MagicMock(return_value=lambda: None)
    hass = MagicMock()
    entry = MagicMock(entry_id="entry-1", options={}, data={}, async_on_unload=MagicMock())
    hass.data = {"labtether": {"entry-1": {"coordinator": coord}}}
    added = []
    await async_setup_entry(hass, entry, added.extend)
    usage = {(e._asset_id, e._metric_key) for e in added if hasattr(e, "_metric_key")}
    assert usage == {
        ("h1", "cpu_used_percent"), ("h1", "memory_used_percent"), ("h1", "disk_used_percent"),
        ("docker-ct-h1-abc", "cpu_used_percent"), ("docker-ct-h1-abc", "memory_used_percent"),
    }
