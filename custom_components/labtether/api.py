"""LabTether API client."""

from __future__ import annotations

import asyncio
from ipaddress import ip_address
import json
import logging
from typing import Any
from urllib.parse import urlparse

from aiohttp import ClientError, ClientSession, ClientSSLError, ClientTimeout

from .const import (
    API_ASSETS,
    API_METRICS_OVERVIEW,
    API_ALERTS_INSTANCES,
    API_ACTIONS_EXECUTE,
    EXCLUDED_SOURCE,
    MAX_ASSETS_PER_RESPONSE,
    MAX_ASSET_FIELD_LENGTH,
    MAX_ASSET_ID_LENGTH,
    TELEMETRY_KINDS,
    CONTROLLABLE_KINDS,
    POWER_ACTION_SOURCES,
)

_LOGGER = logging.getLogger(__name__)

MAX_API_RESPONSE_BYTES = 8 * 1024 * 1024
API_RESPONSE_CHUNK_BYTES = 64 * 1024
API_REQUEST_TIMEOUT_SECONDS = 30


def _validated_asset_id(value: Any, *, field: str = "id") -> str:
    """Return a safe asset identifier or reject the complete API snapshot."""
    if not isinstance(value, str):
        raise LabTetherApiError(f"API returned a non-string asset {field}")
    if not value or value != value.strip() or len(value) > MAX_ASSET_ID_LENGTH:
        raise LabTetherApiError(f"API returned an invalid asset {field}")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise LabTetherApiError(f"API returned an invalid asset {field}")
    return value


def _validate_optional_asset_string(asset: dict[str, Any], field: str) -> None:
    """Validate an optional asset string without rejecting future values."""
    if field not in asset:
        return
    value = asset[field]
    if not isinstance(value, str) or len(value) > MAX_ASSET_FIELD_LENGTH:
        raise LabTetherApiError(f"API returned an invalid asset {field}")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value):
        raise LabTetherApiError(f"API returned an invalid asset {field}")


def hub_origin_is_valid(host: str, *, allow_insecure_http: bool = False) -> bool:
    """Return whether a credential-bearing API origin is safe and unambiguous."""
    if not host or any(ord(character) < 0x20 or ord(character) == 0x7F for character in host):
        return False
    parsed = urlparse(host)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return False
    if (
        parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.params
        or parsed.path not in {"", "/"}
    ):
        return False
    try:
        port = parsed.port
    except ValueError:
        return False
    if port is not None and port == 0:
        return False
    if parsed.scheme == "http" and not allow_insecure_http:
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname != "localhost":
            try:
                if not ip_address(hostname).is_loopback:
                    return False
            except ValueError:
                return False
    return True


class LabTetherApiError(Exception):
    """Exception for LabTether API errors."""


class LabTetherApiClient:
    """Client to interact with the LabTether REST API."""

    def __init__(
        self,
        host: str,
        api_key: str,
        session: ClientSession,
        ignore_cert_errors: bool = False,
        allow_insecure_http: bool = False,
    ) -> None:
        self._host = host.rstrip("/")
        if not hub_origin_is_valid(
            self._host,
            allow_insecure_http=allow_insecure_http,
        ):
            raise LabTetherApiError("Invalid or insecure hub URL")
        self._api_key = api_key
        self._session = session
        self._ignore_cert_errors = ignore_cert_errors

    @property
    def host(self) -> str:
        """Return the hub host URL."""
        return self._host

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    @property
    def _request_kwargs(self) -> dict[str, Any]:
        request_kwargs: dict[str, Any] = {
            "allow_redirects": False,
            "timeout": ClientTimeout(total=API_REQUEST_TIMEOUT_SECONDS),
        }
        if self._ignore_cert_errors:
            request_kwargs["ssl"] = False
        return request_kwargs

    @staticmethod
    async def _read_bounded_json_object(resp) -> dict[str, Any]:
        """Read one bounded JSON object from a successful API response."""
        content_length = resp.content_length
        if content_length is not None and content_length > MAX_API_RESPONSE_BYTES:
            raise LabTetherApiError("API response is too large")
        if resp.content_type != "application/json":
            raise LabTetherApiError("API returned a non-JSON response")

        body = bytearray()
        while len(body) <= MAX_API_RESPONSE_BYTES:
            chunk = await resp.content.read(
                min(API_RESPONSE_CHUNK_BYTES, MAX_API_RESPONSE_BYTES + 1 - len(body))
            )
            if not chunk:
                break
            body.extend(chunk)
        if len(body) > MAX_API_RESPONSE_BYTES:
            raise LabTetherApiError("API response is too large")

        try:
            payload = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError) as err:
            raise LabTetherApiError("API returned invalid JSON") from err
        if not isinstance(payload, dict):
            raise LabTetherApiError("API returned an invalid response object")
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        **kwargs: Any,
    ) -> Any:
        """Make a bounded request, authenticated except for identity discovery."""
        url = f"{self._host}{path}"
        request_kwargs = {
            **kwargs,
            **self._request_kwargs,
            "headers": self._headers if authenticated else {},
        }
        try:
            async with self._session.request(method, url, **request_kwargs) as resp:
                if resp.status == 401:
                    raise LabTetherApiError("Authentication failed")
                if resp.status < 200 or resp.status >= 300:
                    raise LabTetherApiError(f"API request failed (HTTP {resp.status})")
                return await self._read_bounded_json_object(resp)
        except ClientSSLError as err:
            raise LabTetherApiError("TLS certificate verification failed") from err
        except asyncio.TimeoutError as err:
            raise LabTetherApiError("Connection timed out") from err
        except ClientError as err:
            raise LabTetherApiError("Connection failed") from err

    async def _get(self, path: str, params: dict | None = None) -> Any:
        """Make an authenticated GET request."""
        return await self._request("GET", path, params=params)

    async def _post(self, path: str, data: dict) -> Any:
        """Make an authenticated POST request."""
        return await self._request("POST", path, json=data)

    async def async_validate_connection(self) -> bool:
        """Validate the connection to LabTether hub."""
        try:
            await self.async_verify_hub_identity()
            await self.async_get_assets()
            return True
        except LabTetherApiError:
            return False

    async def async_verify_hub_identity(self) -> None:
        """Require the canonical LabTether identity at the configured origin."""
        # Verify the public root before disclosing the API key to this origin.
        data = await self._request("GET", "/", authenticated=False)
        if data.get("service") != "labtether-hub":
            raise LabTetherApiError("The endpoint is not a LabTether hub")

    async def async_get_setup_preview(self) -> dict[str, Any]:
        """Return a setup preview summary."""
        parsed = urlparse(self._host)
        await self.async_verify_hub_identity()
        assets = await self.async_get_assets()
        metrics = await self.async_get_metrics_overview()
        alerts_count = await self.async_get_firing_alerts_count()
        telemetry_assets = [asset for asset in assets if asset.get("type") in TELEMETRY_KINDS]
        switchable_assets = [
            asset
            for asset in assets
            if asset.get("type") in CONTROLLABLE_KINDS
            and asset.get("source", "") in POWER_ACTION_SOURCES
        ]
        sources = sorted({str(asset.get("source", "unknown")) for asset in assets})[:5]
        return {
            "host_label": parsed.netloc or self._host,
            "asset_count": len(assets),
            "telemetry_asset_count": len(telemetry_assets),
            "metric_asset_count": len(metrics),
            "switchable_asset_count": len(switchable_assets),
            "alerts_count": alerts_count,
            "sources_label": ", ".join(sources) if sources else "none",
        }

    async def async_get_assets(self) -> list[dict]:
        """Get all assets, excluding HA-sourced ones to prevent circular mirroring."""
        data = await self._get(API_ASSETS)
        if "assets" not in data:
            raise LabTetherApiError("API response is missing the assets collection")
        assets = data["assets"]
        if not isinstance(assets, list):
            raise LabTetherApiError("API returned an invalid assets collection")
        if len(assets) > MAX_ASSETS_PER_RESPONSE:
            raise LabTetherApiError(
                f"API returned more than {MAX_ASSETS_PER_RESPONSE} assets"
            )
        validated_assets: list[dict[str, Any]] = []
        seen_asset_ids: set[str] = set()
        for asset in assets:
            if not isinstance(asset, dict):
                raise LabTetherApiError("API returned an invalid asset record")

            asset_id = _validated_asset_id(asset.get("id"))
            if asset_id == "hub":
                raise LabTetherApiError("API returned a reserved asset id")
            if asset_id in seen_asset_ids:
                raise LabTetherApiError("API returned duplicate asset IDs")
            seen_asset_ids.add(asset_id)

            for field in ("name", "type", "source", "status"):
                _validate_optional_asset_string(asset, field)
            if "metadata" in asset and not isinstance(asset["metadata"], dict):
                raise LabTetherApiError("API returned invalid asset metadata")

            if asset.get("source") == EXCLUDED_SOURCE:
                continue

            normalized_asset = dict(asset)
            normalized_asset.setdefault("metadata", {})
            validated_assets.append(normalized_asset)

        return validated_assets

    async def async_get_metrics_overview(self) -> dict[str, dict]:
        """Get latest metrics for all assets, keyed by asset_id."""
        data = await self._get(API_METRICS_OVERVIEW)
        entries = data.get("assets", [])
        if not isinstance(entries, list):
            raise LabTetherApiError("API returned an invalid metrics collection")
        if len(entries) > MAX_ASSETS_PER_RESPONSE:
            raise LabTetherApiError(
                f"API returned more than {MAX_ASSETS_PER_RESPONSE} metric records"
            )
        result: dict[str, dict[str, Any]] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                raise LabTetherApiError("API returned an invalid metric record")
            asset_id = _validated_asset_id(entry.get("asset_id"), field="metric asset_id")
            if asset_id in result:
                raise LabTetherApiError("API returned duplicate metric asset IDs")
            metrics = entry.get("metrics", {})
            if not isinstance(metrics, dict):
                raise LabTetherApiError("API returned invalid asset metrics")
            result[asset_id] = metrics
        return result

    async def async_get_firing_alerts_count(self) -> int:
        """Get the count of currently firing alerts."""
        data = await self._get(API_ALERTS_INSTANCES, params={"status": "firing"})
        instances = data.get("instances", [])
        if not isinstance(instances, list):
            raise LabTetherApiError("API returned an invalid alerts collection")
        return sum(
            1
            for instance in instances
            if isinstance(instance, dict) and instance.get("status") == "firing"
        )

    async def async_run_action(
        self,
        asset_id: str,
        action_type: str = "connector_action",
        connector_id: str | None = None,
        action_id: str | None = None,
        params: dict | None = None,
    ) -> dict:
        """Run an action on a LabTether asset."""
        body: dict[str, Any] = {"type": action_type, "asset_id": asset_id}
        if connector_id:
            body["connector_id"] = connector_id
        if action_id:
            body["action_id"] = action_id
        if params:
            body["params"] = params
        return await self._post(API_ACTIONS_EXECUTE, body)
