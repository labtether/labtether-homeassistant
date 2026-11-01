"""Tests for the LabTether DataUpdateCoordinator."""

import pytest
import sys
from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

import labtether.coordinator as coordinator_module
from labtether.coordinator import LabTetherCoordinator, LabTetherData


def test_labtether_data_structure():
    """LabTetherData should hold assets, metrics, and alert count."""
    data = LabTetherData(
        assets=[{"id": "a1", "name": "A1", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}}],
        metrics={"a1": {"cpu_used_percent": 50.0}},
        firing_alerts_count=3,
    )
    assert len(data.assets) == 1
    assert data.metrics["a1"]["cpu_used_percent"] == 50.0
    assert data.firing_alerts_count == 3


def test_labtether_data_get_asset():
    """get_asset should look up by ID."""
    data = LabTetherData(
        assets=[
            {"id": "a1", "name": "A1", "type": "vm", "source": "proxmox", "status": "online", "metadata": {}},
            {"id": "a2", "name": "A2", "type": "vm", "source": "proxmox", "status": "offline", "metadata": {}},
        ],
        metrics={},
        firing_alerts_count=0,
    )
    assert data.get_asset("a1")["name"] == "A1"
    assert data.get_asset("missing") is None


def test_asset_index_preserves_first_match_and_snapshot_ownership():
    """Neither caller edits nor edits through a result can stale indexed IDs."""
    first_asset = {"id": "a1", "name": "First"}
    assets = [first_asset, {"id": "a1", "name": "Second"}]
    data = LabTetherData(assets=assets)

    first_asset["id"] = "changed"
    assets.clear()
    assert len(data.assets) == 2
    assert data.get_asset("a1")["name"] == "First"
    assert data.get_asset("a1") is data.assets[0]
    assert data.get_asset("changed") is None
    with pytest.raises(TypeError):
        data.get_asset("a1")["id"] = "changed"
    with pytest.raises(FrozenInstanceError):
        data.assets = ()


def test_new_snapshot_refreshes_ids_and_values_without_changing_previous_result():
    """Equal-sized inventory changes must not reuse a stale lookup index."""
    assets = [{"id": "a1", "name": "Old"}]
    previous = LabTetherData(assets=assets)
    assets[0] = {"id": "a2", "name": "New"}
    current = LabTetherData(assets=assets)

    assert previous.get_asset("a1")["name"] == "Old"
    assert previous.get_asset("a2") is None
    assert current.get_asset("a1") is None
    assert current.get_asset("a2")["name"] == "New"
    assert current == LabTetherData(assets=assets)


def test_labtether_data_get_metrics():
    """get_metrics should return metrics dict for asset or empty dict."""
    data = LabTetherData(
        assets=[],
        metrics={"a1": {"cpu_used_percent": 50.0}},
        firing_alerts_count=0,
    )
    assert data.get_metrics("a1")["cpu_used_percent"] == 50.0
    assert data.get_metrics("missing") == {}


@pytest.mark.asyncio
async def test_coordinator_limits_asset_identity_churn_and_recovers(monkeypatch):
    """Fresh IDs share a 24-hour ceiling; polling resumes once old IDs age out."""
    monkeypatch.setattr(coordinator_module, "MAX_ASSET_IDENTITIES_PER_ENTRY", 2)
    api = MagicMock()
    api.async_get_assets = AsyncMock(
        side_effect=[
            [{"id": "asset-1"}],
            [{"id": "asset-2"}],
            [{"id": "asset-3"}],
            [{"id": "asset-3"}],
        ]
    )
    api.async_get_metrics_overview = AsyncMock(return_value={})
    api.async_get_firing_alerts_count = AsyncMock(return_value=0)
    coordinator = LabTetherCoordinator(MagicMock(), api, "entry-1")
    now = 1000.0
    coordinator._clock = lambda: now

    await coordinator._async_update_data()
    now += 60
    await coordinator._async_update_data()
    with pytest.raises(Exception, match="asset identity budget exceeded"):
        await coordinator._async_update_data()
    assert coordinator._seen_asset_ids.keys() == {"asset-1", "asset-2"}

    # Recreated containers get new IDs; the old ones must not block polling forever.
    now += coordinator_module.ASSET_IDENTITY_WINDOW_SECONDS + 1
    data = await coordinator._async_update_data()
    assert data.get_asset("asset-3") is not None
    assert coordinator._seen_asset_ids.keys() == {"asset-3"}
