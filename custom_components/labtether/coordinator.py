"""DataUpdateCoordinator for LabTether."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from .const import (
    ASSET_IDENTITY_WINDOW_SECONDS,
    DOMAIN,
    DEFAULT_SCAN_INTERVAL,
    MAX_ASSET_IDENTITIES_PER_ENTRY,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LabTetherData:
    """One poll result with fixed asset membership and indexed asset IDs.

    Copy each asset's fields so caller edits cannot invalidate the index. Nested
    metadata and metrics are read-only by convention, and need no deep copy.
    Publish a new result when assets change, as the coordinator does each poll.
    """

    assets: Sequence[Mapping[str, Any]] = field(default_factory=tuple)
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)
    firing_alerts_count: int = 0
    _assets_by_id: dict[str, Mapping[str, Any]] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Own the asset collection and keep the first match for each ID."""
        assets = tuple(MappingProxyType(dict(asset)) for asset in self.assets)
        index: dict[str, Mapping[str, Any]] = {}
        for asset in assets:
            asset_id = asset.get("id")
            if isinstance(asset_id, str):
                index.setdefault(asset_id, asset)
        object.__setattr__(self, "assets", assets)
        object.__setattr__(self, "_assets_by_id", index)

    def get_asset(self, asset_id: str) -> Mapping[str, Any] | None:
        """Get an asset by ID."""
        return self._assets_by_id.get(asset_id)

    def get_metrics(self, asset_id: str) -> dict[str, Any]:
        """Get metrics for an asset, or empty dict if none."""
        return self.metrics.get(asset_id, {})


def _build_coordinator_class() -> type:
    """Build LabTetherCoordinator class when homeassistant is available."""
    from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
    from homeassistant.exceptions import ConfigEntryAuthFailed
    from .api import LabTetherApiClient, LabTetherApiError

    class LabTetherCoordinator(DataUpdateCoordinator[LabTetherData]):
        """Coordinator to poll LabTether API."""

        def __init__(
            self,
            hass: HomeAssistant,
            api: LabTetherApiClient,
            entry_id: str,
            scan_interval_seconds: int = DEFAULT_SCAN_INTERVAL,
        ) -> None:
            super().__init__(
                hass,
                _LOGGER,
                name=DOMAIN,
                update_interval=timedelta(seconds=scan_interval_seconds),
            )
            self.api = api
            self.entry_id = entry_id
            # Asset ID -> monotonic time it was last seen, within the window.
            self._seen_asset_ids: dict[str, float] = {}
            self._clock = time.monotonic

        async def _async_update_data(self) -> LabTetherData:
            """Fetch data from LabTether API."""
            requests = (
                asyncio.create_task(self.api.async_get_assets()),
                asyncio.create_task(self.api.async_get_metrics_overview()),
                asyncio.create_task(self.api.async_get_firing_alerts_count()),
            )
            try:
                try:
                    assets, metrics, alerts_count = await asyncio.gather(*requests)
                except BaseException:
                    # gather propagates failures without stopping sibling requests.
                    # Finish their cleanup before a failed/cancelled poll returns.
                    for request in requests:
                        request.cancel()
                    await asyncio.gather(*requests, return_exceptions=True)
                    raise
            except LabTetherApiError as err:
                if "Authentication failed" in str(err):
                    raise ConfigEntryAuthFailed from err
                raise UpdateFailed(f"Error communicating with LabTether: {err}") from err

            # Bound how many distinct asset IDs (and so entities) a Hub can
            # create in a day, without stopping for good under normal churn.
            now = self._clock()
            cutoff = now - ASSET_IDENTITY_WINDOW_SECONDS
            seen_asset_ids = {
                asset_id: last_seen
                for asset_id, last_seen in self._seen_asset_ids.items()
                if last_seen >= cutoff
            }
            seen_asset_ids.update(dict.fromkeys((asset["id"] for asset in assets), now))
            if len(seen_asset_ids) > MAX_ASSET_IDENTITIES_PER_ENTRY:
                raise UpdateFailed(
                    "LabTether asset identity budget exceeded: the Hub reported more "
                    f"than {MAX_ASSET_IDENTITIES_PER_ENTRY} different assets in 24 hours. "
                    "Updates resume as older assets age out"
                )

            self._seen_asset_ids = seen_asset_ids
            return LabTetherData(
                assets=assets,
                metrics=metrics,
                firing_alerts_count=alerts_count,
            )

    return LabTetherCoordinator


try:
    LabTetherCoordinator = _build_coordinator_class()
except ImportError:
    # homeassistant not installed — coordinator unavailable outside HA runtime.
    # LabTetherData remains importable for standalone testing.
    LabTetherCoordinator = None  # type: ignore[assignment,misc]
