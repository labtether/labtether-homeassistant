"""Entity names come from the English catalog, and unique IDs stay as in 1.x."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from labtether.binary_sensor import LabTetherAssetStatusSensor, LabTetherHubStatusSensor
from labtether.coordinator import LabTetherData
from labtether.sensor import (
    LabTetherActiveAlertsSensor,
    LabTetherCpuSensor,
    LabTetherDiskSensor,
    LabTetherMemorySensor,
    LabTetherTotalAssetsSensor,
)
from labtether.switch import LabTetherPowerSwitch

COMPONENT = Path(__file__).parent.parent / "custom_components" / "labtether"

# The unique_id each entity had in 1.x. Home Assistant keeps a user's entity_id,
# history and automations only while the unique_id stays the same.
ENTITIES = [
    ("sensor", LabTetherTotalAssetsSensor, (), "labtether_entry-1_hub_total_assets"),
    ("sensor", LabTetherActiveAlertsSensor, (), "labtether_entry-1_hub_active_alerts"),
    ("sensor", LabTetherCpuSensor, ("vm-1",), "labtether_entry-1_vm-1_cpu_used_percent"),
    ("sensor", LabTetherMemorySensor, ("vm-1",), "labtether_entry-1_vm-1_memory_used_percent"),
    ("sensor", LabTetherDiskSensor, ("vm-1",), "labtether_entry-1_vm-1_disk_used_percent"),
    ("binary_sensor", LabTetherHubStatusSensor, (), "labtether_entry-1_hub_status"),
    ("binary_sensor", LabTetherAssetStatusSensor, ("vm-1",), "labtether_entry-1_vm-1_status"),
    ("switch", LabTetherPowerSwitch, ("vm-1",), "labtether_entry-1_vm-1_power"),
]


def _coordinator():
    coordinator = MagicMock()
    coordinator.entry_id = "entry-1"
    coordinator.data = LabTetherData(assets=[], metrics={}, firing_alerts_count=0)
    return coordinator


def test_english_catalog_matches_strings_json():
    """Home Assistant reads translations/en.json, so it must not drift from strings.json."""
    strings = json.loads((COMPONENT / "strings.json").read_text())
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    assert english == strings


@pytest.mark.parametrize(("platform", "entity_class", "args", "unique_id"), ENTITIES)
def test_entity_keeps_unique_id_and_has_an_english_name(platform, entity_class, args, unique_id):
    """A missing name leaves the entity named after its device alone."""
    entity = entity_class(_coordinator(), *args)

    assert entity._attr_unique_id == unique_id
    assert entity._attr_has_entity_name is True
    # A hard-coded name would override the catalog.
    assert not hasattr(entity, "_attr_name")
    english = json.loads((COMPONENT / "translations" / "en.json").read_text())
    assert english["entity"][platform][entity._attr_translation_key]["name"]
