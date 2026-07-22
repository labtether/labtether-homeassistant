#!/usr/bin/env python3
"""Protocol-faithful disposable LabTether API used by live HA Core QA."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import ssl
from threading import Lock
from urllib.parse import urlparse


API_TOKEN = "qa-token"
ACTIONS: list[dict] = []
ACTIONS_LOCK = Lock()

ASSETS = [
    {
        "id": "ltqa-pve-vm-100",
        "name": "LTQA Proxmox VM",
        "type": "vm",
        "source": "proxmox",
        "status": "running",
        "metadata": {"version": "8.4"},
    },
    {
        "id": "ltqa-docker-container",
        "name": "LTQA Docker Container",
        "type": "container",
        "source": "docker",
        "status": "stopped",
        "metadata": {"version": "27.5"},
    },
    {
        "id": "ltqa-circular-ha-entity",
        "name": "LTQA Circular Home Assistant Entity",
        "type": "sensor",
        "source": "home-assistant",
        "status": "online",
        "metadata": {},
    },
]

METRICS = [
    {
        "asset_id": "ltqa-pve-vm-100",
        "metrics": {
            "cpu_used_percent": 11.5,
            "memory_used_percent": 22.5,
            "disk_used_percent": 33.5,
        },
    },
    {
        "asset_id": "ltqa-docker-container",
        "metrics": {
            "cpu_used_percent": 44.5,
            "memory_used_percent": 55.5,
            "disk_used_percent": 66.5,
        },
    },
]


class Handler(BaseHTTPRequestHandler):
    """Serve only the API contract exercised by the integration."""

    server_version = "LTQAFakeHub/1.0"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _json(self, status: HTTPStatus, payload: object) -> None:
        encoded = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _authenticated(self) -> bool:
        if self.headers.get("Authorization") == f"Bearer {API_TOKEN}":
            return True
        self._json(HTTPStatus.UNAUTHORIZED, {"error": "unauthorized"})
        return False

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/qa/status":
            self._json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/qa/actions":
            with ACTIONS_LOCK:
                actions = list(ACTIONS)
            self._json(HTTPStatus.OK, {"actions": actions})
            return
        if path == "/":
            self._json(
                HTTPStatus.OK,
                {
                    "service": "labtether-hub",
                    "message": "LabTether hub API is running.",
                },
            )
            return
        if not self._authenticated():
            return
        if path == "/assets":
            self._json(HTTPStatus.OK, {"assets": ASSETS})
            return
        if path == "/metrics/overview":
            self._json(HTTPStatus.OK, {"assets": METRICS})
            return
        if path == "/alerts/instances":
            self._json(
                HTTPStatus.OK,
                {
                    "instances": [
                        {"id": "ltqa-alert-1", "status": "firing"},
                        {"id": "ltqa-alert-2", "status": "firing"},
                    ]
                },
            )
            return
        self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if not self._authenticated():
            return
        if path != "/actions/execute":
            self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid length"})
            return
        if length <= 0 or length > 16_384:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid body"})
            return
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid json"})
            return
        if not isinstance(payload, dict):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "invalid action"})
            return
        with ACTIONS_LOCK:
            ACTIONS.append(payload)
        self._json(HTTPStatus.OK, {"status": "accepted", "action": payload})


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 18080), Handler)
    tls_cert = os.environ.get("LTQA_TLS_CERT", "")
    tls_key = os.environ.get("LTQA_TLS_KEY", "")
    if bool(tls_cert) != bool(tls_key):
        raise RuntimeError("LTQA_TLS_CERT and LTQA_TLS_KEY must be configured together")
    if tls_cert:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(tls_cert, tls_key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
