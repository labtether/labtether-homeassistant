#!/usr/bin/env python3
"""Onboard a fresh disposable HA Core and write a short-lived QA token."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

import aiohttp


async def _json_request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    *,
    expected: set[int] = {200},
    **kwargs: Any,
) -> Any:
    async with session.request(method, url, **kwargs) as response:
        body = await response.text()
        if response.status not in expected:
            raise RuntimeError(
                f"{method} {url} returned {response.status}: {body[:500]}"
            )
        return json.loads(body) if body else {}


def _write_secret(path: Path, value: str) -> None:
    if path.is_symlink():
        raise RuntimeError(f"refusing symlinked token output {path}")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(path, flags, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.write(descriptor, (value + "\n").encode())
    finally:
        os.close(descriptor)


async def prepare(args: argparse.Namespace) -> None:
    base_url = args.base_url.rstrip("/")
    client_id = base_url + "/"
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        status = await _json_request(session, "GET", base_url + "/api/onboarding")
        user_step = next(step for step in status if step["step"] == "user")
        if user_step["done"]:
            raise RuntimeError("cross-product helper requires a fresh HA config volume")

        created = await _json_request(
            session,
            "POST",
            base_url + "/api/onboarding/users",
            json={
                "name": args.name,
                "username": args.username,
                "password": args.password,
                "client_id": client_id,
                "language": "en",
            },
        )
        token = await _json_request(
            session,
            "POST",
            base_url + "/auth/token",
            data={
                "grant_type": "authorization_code",
                "code": created["auth_code"],
                "client_id": client_id,
            },
        )
        access_token = token["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        await _json_request(
            session,
            "POST",
            base_url + "/api/onboarding/core_config",
            headers=headers,
        )
        await _json_request(
            session,
            "POST",
            base_url + "/api/onboarding/analytics",
            headers=headers,
        )
        await _json_request(
            session,
            "POST",
            base_url + "/api/onboarding/integration",
            headers=headers,
            json={"client_id": client_id, "redirect_uri": client_id},
        )

        ws_url = base_url.replace("http://", "ws://", 1).replace(
            "https://", "wss://", 1
        ) + "/api/websocket"
        async with session.ws_connect(ws_url) as websocket:
            greeting = await websocket.receive_json()
            if greeting.get("type") != "auth_required":
                raise RuntimeError(f"unexpected HA WebSocket greeting: {greeting}")
            await websocket.send_json(
                {"type": "auth", "access_token": access_token}
            )
            authenticated = await websocket.receive_json()
            if authenticated.get("type") != "auth_ok":
                raise RuntimeError(f"HA WebSocket authentication failed: {authenticated}")
            await websocket.send_json(
                {
                    "id": 1,
                    "type": "auth/long_lived_access_token",
                    "client_name": "LabTether disposable cross-product QA",
                    "lifespan": args.token_lifespan_days,
                }
            )
            result = await websocket.receive_json()
            if not result.get("success") or not isinstance(result.get("result"), str):
                raise RuntimeError(f"HA long-lived token request failed: {result}")
            _write_secret(args.token_output, result["result"])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--name", default="LabTether Cross QA")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--token-output", type=Path, required=True)
    parser.add_argument("--token-lifespan-days", type=int, default=7)
    args = parser.parse_args()
    asyncio.run(prepare(args))
    print(f"Disposable HA token written to {args.token_output}")


if __name__ == "__main__":
    main()
