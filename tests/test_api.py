"""Tests for the LabTether API client."""

import json
import pytest
from aiohttp import ClientSession
from unittest.mock import AsyncMock, MagicMock

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from labtether.api import (
    MAX_API_RESPONSE_BYTES,
    LabTetherApiClient,
    LabTetherApiError,
)
from labtether.const import MAX_ASSETS_PER_RESPONSE, MAX_ASSET_ID_LENGTH


@pytest.fixture
def api_client():
    """Create an API client for testing."""
    return LabTetherApiClient(
        host="http://192.168.1.10:8080",
        api_key="test-token-123",
        session=AsyncMock(spec=ClientSession),
        allow_insecure_http=True,
    )


def _mock_response(json_data, status=200, *, content_type="application/json", body=None):
    """Create a mock aiohttp response."""
    encoded = body if body is not None else json.dumps(json_data).encode()
    resp = AsyncMock()
    resp.status = status
    resp.content_type = content_type
    resp.content_length = len(encoded)
    resp.content = MagicMock()
    resp.content.read = AsyncMock(side_effect=[encoded, b""])
    resp.__aenter__ = AsyncMock(return_value=resp)
    resp.__aexit__ = AsyncMock(return_value=False)
    return resp


def test_api_client_requires_explicit_opt_in_for_non_loopback_http():
    """Bearer credentials must not traverse plaintext LAN HTTP by default."""
    with pytest.raises(LabTetherApiError, match="Invalid or insecure"):
        LabTetherApiClient(
            host="http://192.168.1.10:8080",
            api_key="test-token-123",
            session=AsyncMock(spec=ClientSession),
        )

    client = LabTetherApiClient(
        host="http://192.168.1.10:8080",
        api_key="test-token-123",
        session=AsyncMock(spec=ClientSession),
        allow_insecure_http=True,
    )
    assert client.host == "http://192.168.1.10:8080"


@pytest.mark.asyncio
async def test_get_assets_returns_filtered_list(api_client):
    """Valid Home Assistant-sourced assets should be excluded atomically."""
    mock_data = {
        "assets": [
            {"id": "pve-node-1", "name": "Node1", "type": "hypervisor-node", "source": "proxmox", "status": "online", "metadata": {}},
            {"id": "ha-light-1", "name": "Light", "type": "ha-entity", "source": "home-assistant", "status": "online", "metadata": {}},
        ]
    }
    api_client._session.request = MagicMock(return_value=_mock_response(mock_data))

    assets = await api_client.async_get_assets()
    assert len(assets) == 1
    assert assets[0]["id"] == "pve-node-1"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "assets,error",
    [
        ([{"source": "proxmox"}], "asset id"),
        ([{"id": ["not-hashable"], "source": "proxmox"}], "non-string"),
        ([{"id": " asset-1", "source": "proxmox"}], "invalid asset id"),
        ([{"id": "asset\n1", "source": "proxmox"}], "invalid asset id"),
        ([{"id": "a" * (MAX_ASSET_ID_LENGTH + 1), "source": "proxmox"}], "invalid asset id"),
        ([{"id": "asset-1", "type": [], "source": "proxmox"}], "asset type"),
        ([{"id": "asset-1", "source": []}], "asset source"),
        ([{"id": "asset-1", "source": "proxmox", "metadata": None}], "metadata"),
        ([{"id": "asset-1"}, {"id": "asset-1"}], "duplicate"),
        ([{"id": "hub", "source": "proxmox"}], "reserved asset id"),
    ],
)
async def test_get_assets_rejects_malformed_snapshot(api_client, assets, error):
    """Malformed identity and shape data must fail before entity fan-out."""
    api_client._session.request = MagicMock(
        return_value=_mock_response({"assets": assets})
    )

    with pytest.raises(LabTetherApiError, match=error):
        await api_client.async_get_assets()


@pytest.mark.asyncio
async def test_get_assets_rejects_missing_or_over_budget_snapshot(api_client):
    """An omitted or oversized inventory must not become a partial snapshot."""
    for data in (
        {},
        {
            "assets": [
                {"id": f"asset-{index}", "source": "proxmox"}
                for index in range(MAX_ASSETS_PER_RESPONSE + 1)
            ]
        },
        {
            "assets": [
                {"id": f"ha-{index}", "source": "home-assistant"}
                for index in range(MAX_ASSETS_PER_RESPONSE + 1)
            ]
        },
    ):
        api_client._session.request = MagicMock(return_value=_mock_response(data))
        with pytest.raises(LabTetherApiError):
            await api_client.async_get_assets()


@pytest.mark.asyncio
async def test_get_metrics_overview(api_client):
    """Metrics overview should return asset metrics."""
    mock_data = {
        "assets": [
            {
                "asset_id": "pve-node-1",
                "metrics": {"cpu_used_percent": 45.5, "memory_used_percent": 72.0, "disk_used_percent": 30.0},
            }
        ]
    }
    api_client._session.request = MagicMock(return_value=_mock_response(mock_data))

    metrics = await api_client.async_get_metrics_overview()
    assert "pve-node-1" in metrics
    assert metrics["pve-node-1"]["cpu_used_percent"] == 45.5


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "entry",
    [
        {"asset_id": ["not-hashable"], "metrics": {}},
        {"asset_id": "asset-1", "metrics": []},
    ],
)
async def test_get_metrics_overview_rejects_malformed_records(api_client, entry):
    """Metric records must not bypass the asset identity boundary."""
    api_client._session.request = MagicMock(
        return_value=_mock_response({"assets": [entry]})
    )

    with pytest.raises(LabTetherApiError):
        await api_client.async_get_metrics_overview()


@pytest.mark.asyncio
async def test_get_firing_alerts_count(api_client):
    """Should return the count of firing alerts."""
    mock_data = {
        "instances": [
            {"id": "alert-1", "status": "firing"},
            {"id": "alert-2", "status": "firing"},
        ]
    }
    api_client._session.request = MagicMock(return_value=_mock_response(mock_data))

    count = await api_client.async_get_firing_alerts_count()
    assert count == 2


@pytest.mark.asyncio
async def test_validate_connection_success(api_client):
    """Validate connection should require canonical identity and assets."""
    api_client._session.request = MagicMock(side_effect=[
        _mock_response({"service": "labtether-hub"}),
        _mock_response({"assets": []}),
    ])

    result = await api_client.async_validate_connection()
    assert result is True
    assert api_client._session.request.call_args_list[0].kwargs["headers"] == {}
    assert api_client._session.request.call_args_list[1].kwargs["headers"] == {
        "Authorization": "Bearer test-token-123"
    }


@pytest.mark.asyncio
async def test_validate_connection_failure(api_client):
    """An unrelated successful endpoint must not validate as a hub."""
    api_client._session.request = MagicMock(
        return_value=_mock_response({"service": "something-else"})
    )

    result = await api_client.async_validate_connection()
    assert result is False


@pytest.mark.asyncio
async def test_request_refuses_redirects_and_bounds_success_body(api_client):
    """Bearer requests must not follow redirects or accept oversized JSON."""
    oversized = b"{" + (b"x" * MAX_API_RESPONSE_BYTES) + b"}"
    api_client._session.request = MagicMock(
        return_value=_mock_response({}, body=oversized)
    )

    with pytest.raises(LabTetherApiError, match="too large"):
        await api_client.async_get_assets()

    request_kwargs = api_client._session.request.call_args.kwargs
    assert request_kwargs["allow_redirects"] is False
    assert request_kwargs["timeout"].total == 30


@pytest.mark.asyncio
async def test_request_rejects_non_json_and_non_object_success(api_client):
    """Successful status alone must not become an empty inventory."""
    for response in (
        _mock_response({}, content_type="text/html", body=b"ok"),
        _mock_response([], body=b"[]"),
    ):
        api_client._session.request = MagicMock(return_value=response)
        with pytest.raises(LabTetherApiError):
            await api_client.async_get_assets()


@pytest.mark.asyncio
async def test_run_action(api_client):
    """Run action should POST to actions/execute."""
    mock_resp = _mock_response({"job_id": "job-1", "status": "queued"}, status=202)
    api_client._session.request = MagicMock(return_value=mock_resp)

    result = await api_client.async_run_action("pve-vm-100", "connector_action", connector_id="proxmox", action_id="vm.start")
    assert result["job_id"] == "job-1"


@pytest.mark.asyncio
async def test_api_error_on_server_error(api_client):
    """Should raise LabTetherApiError on 500."""
    api_client._session.request = MagicMock(return_value=_mock_response({"error": "internal"}, status=500))

    with pytest.raises(LabTetherApiError):
        await api_client.async_get_assets()


@pytest.mark.asyncio
async def test_ignore_cert_errors_uses_ssl_false():
    """Self-signed hubs should be allowed when ignore_cert_errors is enabled."""
    session = AsyncMock(spec=ClientSession)
    session.request = MagicMock(return_value=_mock_response({"assets": []}))
    client = LabTetherApiClient(
        host="https://lab.local:8443",
        api_key="test-token-123",
        session=session,
        ignore_cert_errors=True,
    )

    await client.async_get_assets()

    assert session.request.call_args.kwargs["ssl"] is False


@pytest.mark.asyncio
async def test_setup_preview_summarizes_assets_metrics_and_alerts():
    """Setup preview should summarize discovered topology."""
    session = AsyncMock(spec=ClientSession)
    client = LabTetherApiClient(
        host="https://lab.local:8443",
        api_key="test-token-123",
        session=session,
    )
    client.async_get_assets = AsyncMock(return_value=[
        {"id": "node-1", "type": "hypervisor-node", "source": "proxmox"},
        {"id": "vm-1", "type": "vm", "source": "proxmox"},
        {"id": "vm-unsupported", "type": "vm", "source": "custom"},
        {"id": "pool-1", "type": "storage-pool", "source": "truenas"},
    ])
    client.async_get_metrics_overview = AsyncMock(return_value={
        "node-1": {"cpu_used_percent": 25.0},
        "vm-1": {"cpu_used_percent": 10.0},
    })
    client.async_get_firing_alerts_count = AsyncMock(return_value=3)
    client.async_verify_hub_identity = AsyncMock()

    preview = await client.async_get_setup_preview()

    client.async_verify_hub_identity.assert_awaited_once()
    assert preview["host_label"] == "lab.local:8443"
    assert preview["asset_count"] == 4
    assert preview["telemetry_asset_count"] == 3
    assert preview["metric_asset_count"] == 2
    assert preview["switchable_asset_count"] == 1
    assert preview["alerts_count"] == 3
