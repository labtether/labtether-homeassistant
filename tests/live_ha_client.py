"""Transport and disposable-container helpers for the live HA scenario."""
from __future__ import annotations

import asyncio
import json
import subprocess
import time
from typing import Any, Awaitable, Callable
from urllib.parse import urlsplit

import aiohttp


class LiveHAClient:
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
