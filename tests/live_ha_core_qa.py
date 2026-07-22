#!/usr/bin/env python3
"""Exercise the custom integration inside an actual disposable HA Core."""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import aiohttp


ASSET_NAMES = ("LTQA Proxmox VM", "LTQA Docker Container")


class LiveHAQA:
    def __init__(
        self,
        base_url: str,
        fake_external_url: str,
        fake_internal_url: str,
        ha_container: str,
        fake_container: str,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.fake_external_url = fake_external_url.rstrip("/")
        self.fake_external_scheme = urlsplit(self.fake_external_url).scheme
        self.fake_internal_url = fake_internal_url.rstrip("/")
        self.ha_container = ha_container
        self.fake_container = fake_container
        self.access_token = ""
        self.entry_id = ""
        self.session: aiohttp.ClientSession

    @property
    def auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    async def request(
        self,
        method: str,
        path: str,
        *,
        json_body: object | None = None,
        data: dict[str, str] | None = None,
        auth: bool = True,
        expected: set[int] | None = None,
        absolute: bool = False,
    ) -> tuple[int, Any]:
        url = path if absolute else self.base_url + path
        headers = self.auth_headers if auth else {}
        request_kwargs: dict[str, Any] = {
            "json": json_body,
            "data": data,
            "headers": headers,
        }
        if absolute and self.fake_external_scheme == "https":
            request_kwargs["ssl"] = False
        async with self.session.request(method, url, **request_kwargs) as response:
            text = await response.text()
            if expected is None:
                expected = {200}
            if response.status not in expected:
                raise AssertionError(
                    f"{method} {url} returned {response.status}, expected {sorted(expected)}: {text[:1000]}"
                )
            if not text:
                return response.status, {}
            try:
                return response.status, json.loads(text)
            except json.JSONDecodeError:
                return response.status, text

    async def wait_for(
        self,
        description: str,
        check: Callable[[], Awaitable[Any]],
        *,
        timeout: float = 90,
        interval: float = 1,
    ) -> Any:
        deadline = time.monotonic() + timeout
        last: object = None
        while time.monotonic() < deadline:
            try:
                result = await check()
                if result:
                    return result
                last = result
            except (aiohttp.ClientError, asyncio.TimeoutError, AssertionError) as err:
                last = err
            await asyncio.sleep(interval)
        raise AssertionError(f"timed out waiting for {description}; last={last!r}")

    async def onboard(self) -> None:
        _, status = await self.request("GET", "/api/onboarding", auth=False)
        user_step = next(step for step in status if step["step"] == "user")
        if user_step["done"]:
            raise AssertionError("disposable HA Core was unexpectedly already onboarded")
        client_id = "http://localhost:8123/"
        _, created = await self.request(
            "POST",
            "/api/onboarding/users",
            auth=False,
            json_body={
                "name": "LTQA Admin",
                "username": "ltqa-admin",
                "password": "LTqa-home-assistant-2026!",
                "client_id": client_id,
                "language": "en",
            },
        )
        _, token = await self.request(
            "POST",
            "/auth/token",
            auth=False,
            data={
                "grant_type": "authorization_code",
                "code": created["auth_code"],
                "client_id": client_id,
            },
        )
        self.access_token = token["access_token"]
        await self.request("GET", "/api/")

    async def create_entry_through_config_flow(self) -> None:
        _, flow = await self.request(
            "POST",
            "/api/config/config_entries/flow",
            json_body={"handler": "labtether", "show_advanced_options": True},
        )
        if flow.get("type") != "form" or flow.get("step_id") != "user":
            raise AssertionError(f"unexpected initial config flow: {flow}")
        flow_path = f"/api/config/config_entries/flow/{flow['flow_id']}"

        tls_rejected_input = {
            "host": self.fake_internal_url,
            "api_key": "wrong-token",
            "name": "LTQA LabTether",
            "ignore_cert_errors": False,
            "allow_insecure_http": False,
        }
        _, tls_rejected = await self.request(
            "POST", flow_path, json_body=tls_rejected_input
        )
        if tls_rejected.get("errors", {}).get("base") != "tls_error":
            raise AssertionError(
                f"self-signed TLS was not rejected clearly: {tls_rejected}"
            )

        bad_input = dict(tls_rejected_input)
        bad_input["ignore_cert_errors"] = True
        _, rejected = await self.request("POST", flow_path, json_body=bad_input)
        if rejected.get("errors", {}).get("base") != "invalid_auth":
            raise AssertionError(f"bad credentials were not rejected: {rejected}")

        good_input = dict(bad_input)
        good_input["api_key"] = "qa-token"
        _, options = await self.request("POST", flow_path, json_body=good_input)
        if options.get("step_id") != "import_options":
            raise AssertionError(f"unexpected import-options flow: {options}")
        placeholders = options.get("description_placeholders", {})
        if placeholders.get("asset_count") != "2" or placeholders.get("alerts_count") != "2":
            raise AssertionError(f"setup preview did not reflect fake hub inventory: {placeholders}")

        _, review = await self.request(
            "POST",
            flow_path,
            json_body={
                "import_status_entities": True,
                "import_telemetry_sensors": True,
                "import_power_switches": True,
                "enable_run_action_service": True,
                "scan_interval_seconds": 5,
            },
        )
        if review.get("step_id") != "review":
            raise AssertionError(f"unexpected review flow: {review}")
        _, created = await self.request("POST", flow_path, json_body={})
        if created.get("type") != "create_entry":
            raise AssertionError(f"config flow did not create entry: {created}")
        self.entry_id = created["result"]["entry_id"]
        await self.wait_for("LabTether entry to load", self.entry_loaded)

    async def entries(self) -> list[dict[str, Any]]:
        _, entries = await self.request(
            "GET", "/api/config/config_entries/entry?domain=labtether"
        )
        return entries

    async def entry_loaded(self) -> bool:
        entries = await self.entries()
        return bool(
            len(entries) == 1
            and entries[0].get("entry_id") == self.entry_id
            and entries[0].get("state") == "loaded"
            and entries[0].get("disabled_by") is None
        )

    async def states(self) -> list[dict[str, Any]]:
        _, states = await self.request("GET", "/api/states")
        return states

    @staticmethod
    def friendly_name(state: dict[str, Any]) -> str:
        return str(state.get("attributes", {}).get("friendly_name", ""))

    async def surface_snapshot(self) -> dict[str, dict[str, Any]]:
        states = await self.states()
        relevant = {
            self.friendly_name(state): state
            for state in states
            if self.friendly_name(state).startswith("LabTether Hub ")
            or any(
                self.friendly_name(state).startswith(asset_name + " ")
                for asset_name in ASSET_NAMES
            )
        }
        return relevant

    async def surface_ready(self) -> dict[str, dict[str, Any]] | bool:
        snapshot = await self.surface_snapshot()
        expected = {
            "LabTether Hub Status",
            "LabTether Hub Total Assets",
            "LabTether Hub Active Alerts",
        }
        for asset_name in ASSET_NAMES:
            expected.update(
                {
                    f"{asset_name} Status",
                    f"{asset_name} CPU Usage",
                    f"{asset_name} Memory Usage",
                    f"{asset_name} Disk Usage",
                    f"{asset_name} Power",
                }
            )
        if not expected.issubset(snapshot):
            missing = sorted(expected - snapshot.keys())
            raise AssertionError(
                f"missing expected LabTether entities {missing}; observed={sorted(snapshot)}"
            )
        if any("Circular" in name for name in snapshot):
            raise AssertionError(f"HA-sourced asset was mirrored circularly: {snapshot}")
        if snapshot["LabTether Hub Status"]["state"] != "on":
            raise AssertionError(
                f"hub status not ready: {snapshot['LabTether Hub Status']}"
            )
        if snapshot["LabTether Hub Total Assets"]["state"] != "2":
            raise AssertionError(
                f"total-assets sensor had unexpected state {snapshot['LabTether Hub Total Assets']}"
            )
        if snapshot["LabTether Hub Active Alerts"]["state"] != "2":
            raise AssertionError(
                f"active-alerts sensor had unexpected state {snapshot['LabTether Hub Active Alerts']}"
            )
        expected_metrics = {
            "LTQA Proxmox VM CPU Usage": 11.5,
            "LTQA Proxmox VM Memory Usage": 22.5,
            "LTQA Proxmox VM Disk Usage": 33.5,
            "LTQA Docker Container CPU Usage": 44.5,
            "LTQA Docker Container Memory Usage": 55.5,
            "LTQA Docker Container Disk Usage": 66.5,
        }
        for name, value in expected_metrics.items():
            if abs(float(snapshot[name]["state"]) - value) > 0.001:
                raise AssertionError(
                    f"metric {name} had unexpected state {snapshot[name]}"
                )
        if snapshot["LTQA Proxmox VM Status"]["state"] != "on":
            raise AssertionError(
                f"Proxmox status had unexpected state {snapshot['LTQA Proxmox VM Status']}"
            )
        if snapshot["LTQA Docker Container Status"]["state"] != "off":
            raise AssertionError(
                f"Docker status had unexpected state {snapshot['LTQA Docker Container Status']}"
            )
        if snapshot["LTQA Proxmox VM Power"]["state"] != "on":
            raise AssertionError(
                f"Proxmox power had unexpected state {snapshot['LTQA Proxmox VM Power']}"
            )
        if snapshot["LTQA Docker Container Power"]["state"] != "off":
            raise AssertionError(
                f"Docker power had unexpected state {snapshot['LTQA Docker Container Power']}"
            )
        return snapshot

    async def service_registered(self, expected: bool = True) -> bool:
        _, services = await self.request("GET", "/api/services")
        registered = any(
            service.get("domain") == "labtether"
            and "run_action" in service.get("services", {})
            for service in services
        )
        return registered is expected

    async def entity_registry_entries(self) -> list[dict[str, Any]]:
        """Return the live HA entity registry through its user-facing WebSocket API."""
        ws_url = self.base_url.replace("http://", "ws://", 1).replace(
            "https://", "wss://", 1
        ) + "/api/websocket"
        async with self.session.ws_connect(ws_url) as ws:
            required = await ws.receive_json()
            if required.get("type") != "auth_required":
                raise AssertionError(f"unexpected websocket greeting: {required}")
            await ws.send_json({"type": "auth", "access_token": self.access_token})
            authenticated = await ws.receive_json()
            if authenticated.get("type") != "auth_ok":
                raise AssertionError(
                    f"websocket authentication failed: {authenticated}"
                )
            await ws.send_json({"id": 1, "type": "config/entity_registry/list"})
            result = await ws.receive_json()
            if not result.get("success") or not isinstance(result.get("result"), list):
                raise AssertionError(f"entity-registry command failed: {result}")
            return result["result"]

    def docker(self, action: str, container: str) -> None:
        allowed = {
            "restart": "ltqa-ha-core-",
            "stop": "ltqa-ha-fake-",
            "start": "ltqa-ha-fake-",
        }
        prefix = allowed.get(action)
        if prefix is None or not container.startswith(prefix):
            raise AssertionError(
                f"refusing unsafe docker mutation action={action!r} container={container!r}"
            )
        subprocess.run(
            ["docker", action, container],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
        )

    def refresh_fake_external_url(self) -> None:
        if not self.fake_container.startswith("ltqa-ha-fake-"):
            raise AssertionError(
                f"refusing to inspect unsafe fake container {self.fake_container!r}"
            )
        published = subprocess.check_output(
            ["docker", "port", self.fake_container, "18080/tcp"], text=True
        ).strip()
        if not published or ":" not in published:
            raise AssertionError(f"fake hub has no published port: {published!r}")
        port = published.rsplit(":", 1)[1]
        if not port.isdecimal():
            raise AssertionError(f"fake hub published an invalid port: {published!r}")
        self.fake_external_url = f"{self.fake_external_scheme}://127.0.0.1:{port}"

    async def verify_failure_and_reconnect(self) -> None:
        self.docker("stop", self.fake_container)

        async def unavailable() -> bool:
            snapshot = await self.surface_snapshot()
            hub = snapshot.get("LabTether Hub Status")
            return bool(hub and hub.get("state") == "unavailable")

        await self.wait_for("coordinator failure state", unavailable, timeout=45)
        self.docker("start", self.fake_container)
        self.refresh_fake_external_url()
        await self.wait_for("coordinator recovery", self.surface_ready, timeout=60)

    async def exercise_actions(self) -> None:
        await self.request(
            "POST",
            "/api/services/labtether/run_action",
            json_body={
                "asset_id": "ltqa-pve-vm-100",
                "action": "vm.start",
            },
        )
        status, _ = await self.request(
            "POST",
            "/api/services/labtether/run_action",
            json_body={
                "asset_id": "ltqa-pve-vm-100",
                "action": "vm.reboot",
            },
            expected={400},
        )
        if status < 400:
            raise AssertionError("disallowed generic action unexpectedly succeeded")

        snapshot = await self.surface_snapshot()
        await self.request(
            "POST",
            "/api/services/switch/turn_off",
            json_body={"entity_id": snapshot["LTQA Proxmox VM Power"]["entity_id"]},
        )
        await self.request(
            "POST",
            "/api/services/switch/turn_on",
            json_body={"entity_id": snapshot["LTQA Docker Container Power"]["entity_id"]},
        )

        async def actions_ready() -> list[dict[str, Any]] | bool:
            _, payload = await self.request(
                "GET",
                self.fake_external_url + "/qa/actions",
                auth=False,
                absolute=True,
            )
            actions = payload.get("actions", [])
            return actions if len(actions) >= 3 else False

        actions = await self.wait_for("three bounded action requests", actions_ready)
        expected = {
            ("ltqa-pve-vm-100", "proxmox", "vm.start"),
            ("ltqa-pve-vm-100", "proxmox", "vm.stop"),
            ("ltqa-docker-container", "docker", "container.start"),
        }
        observed = {
            (action.get("asset_id"), action.get("connector_id"), action.get("action_id"))
            for action in actions
        }
        if not expected.issubset(observed):
            raise AssertionError(f"unexpected action payloads: {actions}")
        if any(action.get("params") for action in actions):
            raise AssertionError(f"unexpected arbitrary action params: {actions}")

    async def update_options(self, *, sensors: bool, service: bool) -> None:
        _, flow = await self.request(
            "POST",
            "/api/config/config_entries/options/flow",
            json_body={"handler": self.entry_id},
        )
        if flow.get("step_id") != "init":
            raise AssertionError(f"unexpected options flow: {flow}")
        _, result = await self.request(
            "POST",
            f"/api/config/config_entries/options/flow/{flow['flow_id']}",
            json_body={
                "ignore_cert_errors": True,
                "import_status_entities": True,
                "import_telemetry_sensors": sensors,
                "import_power_switches": True,
                "enable_run_action_service": service,
                "scan_interval_seconds": 5,
            },
        )
        if result.get("type") != "create_entry":
            raise AssertionError(f"options flow did not complete: {result}")
        await self.wait_for("entry reload after options update", self.entry_loaded)

    async def verify_options_reload(self) -> None:
        await self.update_options(sensors=False, service=False)

        async def disabled_surface() -> bool:
            snapshot = await self.surface_snapshot()
            registry_entries = await self.entity_registry_entries()
            no_sensors = not any(
                name.endswith(("CPU Usage", "Memory Usage", "Disk Usage"))
                or name in {"LabTether Hub Total Assets", "LabTether Hub Active Alerts"}
                for name in snapshot
            )
            no_registry_ghosts = not any(
                entry.get("config_entry_id") == self.entry_id
                and entry.get("platform") == "labtether"
                and str(entry.get("entity_id", "")).startswith("sensor.")
                for entry in registry_entries
            )
            return (
                no_sensors
                and no_registry_ghosts
                and await self.service_registered(False)
            )

        await self.wait_for("sensor and service options to unload", disabled_surface)
        await self.update_options(sensors=True, service=True)
        await self.wait_for("sensor and service options to reload", self.surface_ready)
        await self.wait_for("run_action service to return", self.service_registered)

    async def reconfigure(self) -> None:
        _, flow = await self.request(
            "POST",
            "/api/config/config_entries/flow",
            json_body={"handler": "labtether", "entry_id": self.entry_id},
        )
        if flow.get("step_id") != "reconfigure":
            raise AssertionError(f"unexpected reconfigure flow: {flow}")
        _, result = await self.request(
            "POST",
            f"/api/config/config_entries/flow/{flow['flow_id']}",
            json_body={
                "host": self.fake_internal_url,
                "api_key": "qa-token",
                "name": "LTQA LabTether Reconfigured",
                "ignore_cert_errors": True,
                "allow_insecure_http": False,
            },
        )
        if result.get("type") != "abort" or result.get("reason") != "reconfigure_successful":
            raise AssertionError(f"reconfigure did not complete: {result}")
        await self.wait_for("reconfigured entry to reload", self.surface_ready)

    async def websocket_disable(self, disabled_by: str | None) -> dict[str, Any]:
        ws_url = self.base_url.replace("http://", "ws://", 1).replace(
            "https://", "wss://", 1
        ) + "/api/websocket"
        async with self.session.ws_connect(ws_url) as ws:
            required = await ws.receive_json()
            if required.get("type") != "auth_required":
                raise AssertionError(f"unexpected websocket greeting: {required}")
            await ws.send_json({"type": "auth", "access_token": self.access_token})
            authenticated = await ws.receive_json()
            if authenticated.get("type") != "auth_ok":
                raise AssertionError(f"websocket authentication failed: {authenticated}")
            await ws.send_json(
                {
                    "id": 1,
                    "type": "config_entries/disable",
                    "entry_id": self.entry_id,
                    "disabled_by": disabled_by,
                }
            )
            result = await ws.receive_json()
            if not result.get("success"):
                raise AssertionError(f"config entry disable command failed: {result}")
            command_result = result.get("result", {})
            if command_result.get("require_restart"):
                raise AssertionError(
                    f"config entry disable required a restart: {command_result}"
                )
            return command_result

    async def verify_unload_enable(self) -> None:
        await self.websocket_disable("user")

        async def unloaded() -> bool:
            entries = await self.entries()
            snapshot = await self.surface_snapshot()
            entry = next(
                (item for item in entries if item.get("entry_id") == self.entry_id),
                None,
            )
            service_absent = await self.service_registered(False)
            if (
                entry is not None
                and entry.get("disabled_by") == "user"
                and not snapshot
                and service_absent
            ):
                return True
            raise AssertionError(
                "entry did not fully unload: "
                f"entry={entry!r}, surfaces={sorted(snapshot)}, "
                f"service_absent={service_absent}"
            )

        await self.wait_for("disabled config entry to unload", unloaded, timeout=30)
        await self.websocket_disable(None)
        await self.wait_for("re-enabled config entry to load", self.surface_ready)
        await self.wait_for("re-enabled service to load", self.service_registered)

    async def restart_home_assistant(self) -> None:
        self.docker("restart", self.ha_container)
        await self.refresh_ha_external_url()

        async def api_ready() -> bool:
            status, _ = await self.request("GET", "/api/", expected={200, 401})
            return status == 200

        await self.wait_for("Home Assistant API after restart", api_ready, timeout=120)
        await self.wait_for("persisted LabTether entry after restart", self.surface_ready, timeout=120)
        await self.wait_for("persisted run_action service after restart", self.service_registered)
        await self.request(
            "POST",
            "/api/services/labtether/run_action",
            json_body={
                "asset_id": "ltqa-docker-container",
                "action": "container.stop",
            },
        )

    async def refresh_ha_external_url(self) -> None:
        completed = subprocess.run(
            ["docker", "port", self.ha_container, "8123/tcp"],
            check=True,
            capture_output=True,
            text=True,
        )
        port = completed.stdout.strip().rsplit(":", 1)[-1]
        if not port.isdigit():
            raise AssertionError(f"unexpected Home Assistant port mapping: {completed.stdout!r}")
        self.base_url = f"http://127.0.0.1:{port}"

    async def remove_entry(self) -> None:
        await self.request(
            "DELETE", f"/api/config/config_entries/entry/{self.entry_id}"
        )

        async def removed() -> bool:
            return not await self.entries() and not await self.surface_snapshot() and await self.service_registered(False)

        await self.wait_for("config entry removal", removed)

    async def run(self) -> None:
        timeout = aiohttp.ClientTimeout(total=20)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            self.session = session
            await self.onboard()
            await self.create_entry_through_config_flow()
            await self.wait_for("initial entity surface", self.surface_ready)
            await self.wait_for("initial run_action service", self.service_registered)
            await self.verify_failure_and_reconnect()
            await self.exercise_actions()
            await self.verify_options_reload()
            await self.reconfigure()
            await self.request(
                "POST", f"/api/config/config_entries/entry/{self.entry_id}/reload"
            )
            await self.wait_for("explicit REST reload", self.surface_ready)
            await self.verify_unload_enable()
            await self.restart_home_assistant()
            await self.remove_entry()


async def async_main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--fake-external-url", required=True)
    parser.add_argument("--fake-internal-url", required=True)
    parser.add_argument("--ha-container", required=True)
    parser.add_argument("--fake-container", required=True)
    args = parser.parse_args()
    qa = LiveHAQA(
        args.base_url,
        args.fake_external_url,
        args.fake_internal_url,
        args.ha_container,
        args.fake_container,
    )
    await qa.run()
    print("Home Assistant Core live integration QA passed")


if __name__ == "__main__":
    asyncio.run(async_main())
