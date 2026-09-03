"""Tests for the LabTether DataUpdateCoordinator."""

import pytest
import sys
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
async def test_coordinator_rejects_asset_identity_churn(monkeypatch):
    """Fresh IDs across bounded polls must have one shared lifetime ceiling."""
    monkeypatch.setattr(coordinator_module, "MAX_ASSET_IDENTITIES_PER_ENTRY", 2)
    api = MagicMock()
    api.async_get_assets = AsyncMock(
        side_effect=[
            [{"id": "asset-1"}],
            [{"id": "asset-2"}],
            [{"id": "asset-3"}],
        ]
    )
    api.async_get_metrics_overview = AsyncMock(return_value={})
    api.async_get_firing_alerts_count = AsyncMock(return_value=0)
    coordinator = LabTetherCoordinator(MagicMock(), api, "entry-1")

    await coordinator._async_update_data()
    await coordinator._async_update_data()
    with pytest.raises(Exception, match="asset identity budget exceeded"):
        await coordinator._async_update_data()

    assert coordinator._seen_asset_ids == {"asset-1", "asset-2"}
