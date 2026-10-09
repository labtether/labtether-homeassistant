"""Real TLS handshake checks for per-Hub CA trust."""

import asyncio
from pathlib import Path
import ssl
import subprocess
import sys

import aiohttp
import certifi
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "custom_components"))

from labtether.api import HubCACertificateError, LabTetherApiClient, LabTetherApiError
from labtether.tls import load_hub_ca_context


def _make_certificates(folder: Path) -> tuple[Path, Path, Path]:
    ca = folder / "ca.pem"
    key = folder / "ca.key"
    server_key = folder / "server.key"
    request = folder / "server.csr"
    server_cert = folder / "server.pem"
    commands = (
        ["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
         "-days", "1", "-subj", "/CN=LabTether Test CA",
         "-addext", "basicConstraints=critical,CA:TRUE,pathlen:0",
         "-addext", "keyUsage=critical,keyCertSign,cRLSign",
         "-keyout", str(key), "-out", str(ca)],
        ["openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-sha256",
         "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
         "-addext", "extendedKeyUsage=serverAuth",
         "-keyout", str(server_key), "-out", str(request)],
        ["openssl", "x509", "-req", "-sha256", "-days", "1",
         "-in", str(request), "-CA", str(ca), "-CAkey", str(key),
         "-CAcreateserial", "-copy_extensions", "copy", "-out", str(server_cert)],
    )
    for command in commands:
        subprocess.run(command, check=True, capture_output=True)
    return ca, server_cert, server_key


async def _hub_response(_reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    body = b'{"service":"labtether-hub"}'
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
        + body
    )
    await writer.drain()
    writer.close()
    await writer.wait_closed()


@pytest.mark.asyncio
async def test_custom_ca_preserves_certificate_and_hostname_checks(tmp_path):
    ca, server_cert, server_key = _make_certificates(tmp_path)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(server_cert, server_key)
    server = await asyncio.start_server(_hub_response, "127.0.0.1", 0, ssl=server_context)
    port = server.sockets[0].getsockname()[1]
    default_context = ssl.create_default_context(cafile=certifi.where())
    try:
        async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=default_context)) as session:
            untrusted = LabTetherApiClient(f"https://localhost:{port}", "test-key", session)
            with pytest.raises(LabTetherApiError, match="TLS certificate verification failed"):
                await untrusted.async_verify_hub_identity()

            trusted_context = load_hub_ca_context(str(ca))
            assert trusted_context.verify_mode == ssl.CERT_REQUIRED
            assert trusted_context.check_hostname is True
            trusted = LabTetherApiClient(
                f"https://localhost:{port}", "test-key", session,
                ssl_context=trusted_context,
            )
            await trusted.async_verify_hub_identity()

            wrong_name = LabTetherApiClient(
                f"https://127.0.0.1:{port}", "test-key", session,
                ssl_context=trusted_context,
            )
            with pytest.raises(LabTetherApiError, match="TLS certificate verification failed"):
                await wrong_name.async_verify_hub_identity()
    finally:
        server.close()
        await server.wait_closed()


def test_custom_ca_requires_valid_absolute_pem(tmp_path):
    with pytest.raises(HubCACertificateError):
        load_hub_ca_context("relative/ca.pem")
    bad = tmp_path / "bad.pem"
    bad.write_text("not a certificate")
    with pytest.raises(HubCACertificateError):
        load_hub_ca_context(str(bad))
