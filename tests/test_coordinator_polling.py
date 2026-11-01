"""Check complete polling results and cleanup of concurrent requests."""

import asyncio
from unittest.mock import MagicMock

import pytest
from homeassistant.exceptions import ConfigEntryAuthFailed

from labtether.api import LabTetherApiError
from labtether.coordinator import LabTetherCoordinator, LabTetherData


class ControlledAPI:
    """Three independently released requests, with observable async cleanup."""

    def __init__(self):
        self.responses = {
            "assets": [{"id": "new-asset"}],
            "metrics": {"new-asset": {"cpu_used_percent": 12}},
            "alerts": 3,
        }
        self.release = {name: asyncio.Event() for name in self.responses}
        self.errors = {}
        self.started = set()
        self.cleaning = set()
        self.finished = set()
        self.all_started = asyncio.Event()
        self.all_cleaning = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_release.set()

    async def _fetch(self, name):
        self.started.add(name)
        if len(self.started) == 3:
            self.all_started.set()
        try:
            await self.release[name].wait()
            if name in self.errors:
                raise self.errors[name]
            return self.responses[name]
        finally:
            self.cleaning.add(name)
            if len(self.cleaning) == 3:
                self.all_cleaning.set()
            await self.cleanup_release.wait()
            await asyncio.sleep(0)
            self.finished.add(name)

    async def async_get_assets(self):
        return await self._fetch("assets")

    async def async_get_metrics_overview(self):
        return await self._fetch("metrics")

    async def async_get_firing_alerts_count(self):
        return await self._fetch("alerts")


async def _wait(event):
    """Fail fast if the coordinator waits on requests one at a time."""
    await asyncio.wait_for(event.wait(), timeout=1)


async def _finish(poll, api):
    """Keep a failed assertion from leaving background requests behind."""
    api.cleanup_release.set()
    for event in api.release.values():
        event.set()
    if not poll.done():
        poll.cancel()
    await asyncio.gather(poll, return_exceptions=True)


@pytest.mark.asyncio
async def test_poll_starts_three_requests_and_returns_only_complete_snapshot():
    api = ControlledAPI()
    coordinator = LabTetherCoordinator(MagicMock(), api, "entry-1")
    poll = asyncio.create_task(coordinator._async_update_data())
    try:
        await _wait(api.all_started)
        api.release["assets"].set()
        await asyncio.sleep(0)
        assert not poll.done()
        assert coordinator._seen_asset_ids == {}

        api.release["metrics"].set()
        api.release["alerts"].set()
        data = await asyncio.wait_for(poll, timeout=1)
        assert data.get_asset("new-asset")["id"] == "new-asset"
        assert data.get_metrics("new-asset") == {"cpu_used_percent": 12}
        assert data.firing_alerts_count == 3
        assert coordinator._seen_asset_ids.keys() == {"new-asset"}
        assert api.finished == set(api.responses)
    finally:
        await _finish(poll, api)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected", "message"),
    [
        (LabTetherApiError("Unavailable"), Exception, "Error communicating"),
        (LabTetherApiError("Authentication failed"), ConfigEntryAuthFailed, None),
        (RuntimeError("Unexpected failure"), RuntimeError, "Unexpected failure"),
        (asyncio.CancelledError(), asyncio.CancelledError, None),
    ],
)
async def test_request_failure_cleans_siblings_and_keeps_previous_state(
    error, expected, message
):
    api = ControlledAPI()
    api.errors["metrics"] = error
    coordinator = LabTetherCoordinator(MagicMock(), api, "entry-1")
    previous = LabTetherData(assets=[{"id": "previous"}])
    coordinator.data = previous
    coordinator._seen_asset_ids = {"previous": 0.0}
    poll = asyncio.create_task(coordinator._async_update_data())
    try:
        await _wait(api.all_started)
        api.release["metrics"].set()
        with pytest.raises(expected, match=message):
            await asyncio.wait_for(poll, timeout=1)
        assert api.finished == set(api.responses)
        assert coordinator.data is previous
        assert coordinator._seen_asset_ids == {"previous": 0.0}
    finally:
        await _finish(poll, api)


@pytest.mark.asyncio
async def test_cancelled_poll_waits_for_all_request_cleanup():
    api = ControlledAPI()
    api.cleanup_release.clear()
    coordinator = LabTetherCoordinator(MagicMock(), api, "entry-1")
    poll = asyncio.create_task(coordinator._async_update_data())
    try:
        await _wait(api.all_started)
        poll.cancel()
        await _wait(api.all_cleaning)
        assert not poll.done()
        assert api.finished == set()

        api.cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(poll, timeout=1)
        assert api.finished == set(api.responses)
        assert coordinator._seen_asset_ids == {}
    finally:
        await _finish(poll, api)
