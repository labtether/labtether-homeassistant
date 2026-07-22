#!/usr/bin/env python3
"""Minimal TLS reverse proxy for the disposable Home Assistant QA target."""

from __future__ import annotations

import argparse
import ssl
from collections.abc import AsyncIterator, Mapping
from typing import Final

import aiohttp
from aiohttp import web


HOP_BY_HOP_HEADERS: Final = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
CLIENT_KEY = web.AppKey("client", aiohttp.ClientSession)
UPSTREAM_KEY = web.AppKey("upstream", str)


def _forward_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {
        name: value
        for name, value in headers.items()
        if name.lower() not in HOP_BY_HOP_HEADERS
        and name.lower() not in {"host", "content-length"}
    }


async def _proxy(request: web.Request) -> web.Response:
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return web.Response(status=426, text="WebSocket proxying is not required for connector QA")

    session = request.app[CLIENT_KEY]
    upstream = request.app[UPSTREAM_KEY]
    body = await request.read()
    try:
        async with session.request(
            request.method,
            upstream + str(request.rel_url),
            headers=_forward_headers(request.headers),
            data=body or None,
            allow_redirects=False,
        ) as response:
            payload = await response.read()
            headers = {
                name: value
                for name, value in response.headers.items()
                if name.lower() not in HOP_BY_HOP_HEADERS
                and name.lower() not in {"content-length"}
            }
            return web.Response(body=payload, status=response.status, headers=headers)
    except (aiohttp.ClientError, TimeoutError):
        return web.Response(status=502, text="Disposable Home Assistant upstream unavailable")


async def _client_context(app: web.Application) -> AsyncIterator[None]:
    app[CLIENT_KEY] = aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=30),
        trust_env=False,
        auto_decompress=False,
        skip_auto_headers={"Accept-Encoding"},
    )
    yield
    await app[CLIENT_KEY].close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--listen-host", default="0.0.0.0")
    parser.add_argument("--listen-port", type=int, default=8443)
    args = parser.parse_args()

    app = web.Application(client_max_size=32 * 1024 * 1024)
    app[UPSTREAM_KEY] = args.upstream.rstrip("/")
    app.router.add_route("*", "/{path:.*}", _proxy)
    app.cleanup_ctx.append(_client_context)

    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.minimum_version = ssl.TLSVersion.TLSv1_2
    tls.load_cert_chain(args.cert, args.key)
    web.run_app(
        app,
        host=args.listen_host,
        port=args.listen_port,
        ssl_context=tls,
        print=None,
        access_log=None,
    )


if __name__ == "__main__":
    main()
