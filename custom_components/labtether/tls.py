"""Per-Hub CA trust without changing Home Assistant's shared TLS context."""

from __future__ import annotations

import os
from pathlib import Path
import ssl

from .api import HubCACertificateError

MAX_CA_BUNDLE_BYTES = 1024 * 1024


def load_hub_ca_context(ca_certificate: str) -> ssl.SSLContext:
    """Add one operator-supplied CA to the system and configured CA roots."""
    path = Path(ca_certificate)
    try:
        if not path.is_absolute() or not path.is_file():
            raise ValueError("expected an absolute path to a CA PEM file")
        if path.stat().st_size > MAX_CA_BUNDLE_BYTES:
            raise ValueError("CA PEM file is too large")
        context = ssl.create_default_context()
        if default_bundle := os.environ.get("REQUESTS_CA_BUNDLE"):
            context.load_verify_locations(cafile=default_bundle)
        context.load_verify_locations(cafile=str(path))
        context.set_alpn_protocols(["http/1.1"])
        return context
    except (OSError, ValueError, ssl.SSLError) as err:
        raise HubCACertificateError("CA certificate file is missing or invalid") from err


async def async_load_hub_ca_context(hass, ca_certificate: str) -> ssl.SSLContext | None:
    """Read the CA file in Home Assistant's executor, never on its event loop."""
    if not ca_certificate:
        return None
    return await hass.async_add_executor_job(load_hub_ca_context, ca_certificate)
