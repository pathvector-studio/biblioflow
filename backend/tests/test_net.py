from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx
import pytest

from app.core import net
from app.core.net import (
    BlockedDestination,
    SafeNetworkBackend,
    check_ip,
    safe_client,
    safe_get_bytes,
    safe_stream,
    validate_url,
)
from app.schemas.metadata import UpstreamError


@pytest.mark.parametrize(
    "ip",
    [
        "127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.1.1", "169.254.169.254", "100.64.0.1",
        "0.0.0.0", "::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "fd00::1", "fe80::1",
        "64:ff9b::7f00:1", "224.0.0.1", "2002:7f00:1::1", "::",
    ],
)
def test_check_ip_blocks(ip: str) -> None:
    with pytest.raises(BlockedDestination):
        check_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize("ip", ["8.8.8.8", "2606:4700::1111", "::ffff:8.8.8.8"])
def test_check_ip_allows(ip: str) -> None:
    check_ip(ipaddress.ip_address(ip))


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd", "ftp://example.com/x", "http://user:pw@example.com/",
        "https://example.com:22/", "http://example.com:8080/", "http://127.0.0.1/",
        "http://[::1]/", "http://[::ffff:7f00:1]/", "https:///nohost",
    ],
)
def test_validate_url_rejects(url: str) -> None:
    with pytest.raises(BlockedDestination):
        validate_url(url)


def test_validate_url_ok_and_allowlist() -> None:
    assert validate_url("https://Example.com:443/x").host == "example.com"
    assert validate_url("http://example.com:80/").scheme == "http"
    validate_url("https://api.crossref.org/works", {"api.crossref.org"})
    with pytest.raises(BlockedDestination):
        validate_url("https://evil.test/", {"api.crossref.org"})


def _fake_getaddrinfo(mapping: dict[str, list[str]]):
    async def fake(host, port, **kw):
        return [
            (socket.AF_INET6 if ":" in a else socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port))
            for a in mapping[host]
        ]

    return fake


async def test_backend_blocks_mixed_resolution(monkeypatch) -> None:
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(
        loop, "getaddrinfo", _fake_getaddrinfo({"mixed.test": ["8.8.8.8", "10.0.0.5"], "evil.test": ["127.0.0.1"]})
    )
    backend = SafeNetworkBackend()
    for host in ("mixed.test", "evil.test"):
        with pytest.raises(BlockedDestination):
            await backend.connect_tcp(host, 80)
    with pytest.raises(BlockedDestination):
        await backend.connect_unix_socket("/var/run/docker.sock")


async def test_dns_name_to_loopback_blocked_via_client(monkeypatch) -> None:
    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", _fake_getaddrinfo({"evil.test": ["127.0.0.1"]}))
    async with safe_client() as client:
        with pytest.raises(BlockedDestination):
            await client.get("http://evil.test/")


async def test_real_request_to_loopback_never_connects() -> None:
    connections = 0

    async def handler(reader, writer):
        nonlocal connections
        connections += 1
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with safe_client() as client:
            with pytest.raises(BlockedDestination):
                await client.get(f"http://127.0.0.1:{port}/")
        await asyncio.sleep(0.05)
    finally:
        server.close()
        await server.wait_closed()
    assert connections == 0


def test_client_settings() -> None:
    c = safe_client(allowed_hosts={"A.test"})
    assert c.allowed_hosts == {"a.test"}
    assert c.follow_redirects is False
    assert c.headers["user-agent"].startswith("BiblioFlow/0.2")


def _mock_client(handler, allowed=None) -> httpx.AsyncClient:
    c = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    c.allowed_hosts = allowed  # type: ignore[attr-defined]
    return c


async def test_stream_follows_safe_redirect() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/a":
            return httpx.Response(302, headers={"location": "/b"})
        return httpx.Response(200, content=b"ok")

    async with _mock_client(handler) as c:
        async with safe_stream(c, "https://example.com/a") as r:
            assert r.url.path == "/b"
            assert await r.aread() == b"ok"


@pytest.mark.parametrize("target", ["http://169.254.169.254/latest", "http://[::1]/", "ftp://example.com/", "https://example.com:22/"])
async def test_stream_blocks_bad_redirect(target: str) -> None:
    async with _mock_client(lambda req: httpx.Response(302, headers={"location": target})) as c:
        with pytest.raises(BlockedDestination):
            async with safe_stream(c, "https://example.com/a"):
                pass


async def test_stream_blocks_non_allowlisted_redirect() -> None:
    async with _mock_client(lambda req: httpx.Response(301, headers={"location": "https://other.test/"}), {"example.com"}) as c:
        with pytest.raises(BlockedDestination):
            async with safe_stream(c, "https://example.com/a"):
                pass


async def test_stream_too_many_redirects() -> None:
    async with _mock_client(lambda req: httpx.Response(302, headers={"location": "/loop"})) as c:
        with pytest.raises(BlockedDestination):
            async with safe_stream(c, "https://example.com/a"):
                pass


async def test_get_bytes_cap_and_status() -> None:
    async with _mock_client(lambda req: httpx.Response(200, content=b"x" * 100)) as c:
        _, body = await safe_get_bytes(c, "https://example.com/", max_bytes=100)
        assert len(body) == 100
        with pytest.raises(UpstreamError):
            await safe_get_bytes(c, "https://example.com/", max_bytes=99)
    async with _mock_client(lambda req: httpx.Response(500)) as c:
        with pytest.raises(UpstreamError):
            await safe_get_bytes(c, "https://example.com/", max_bytes=10)


async def test_get_bytes_chunked_cap() -> None:
    async def gen():
        for _ in range(5):
            yield b"y" * 50

    async with _mock_client(lambda req: httpx.Response(200, content=gen())) as c:
        with pytest.raises(UpstreamError):
            await safe_get_bytes(c, "https://example.com/", max_bytes=120)


async def test_get_bytes_transport_error_is_upstream() -> None:
    def boom(req):
        raise httpx.ConnectError("nope")

    async with _mock_client(boom) as c:
        with pytest.raises(UpstreamError):
            await safe_get_bytes(c, "https://example.com/", max_bytes=10)


def test_module_exports() -> None:
    assert net.ALLOWED_PORTS == {80, 443}
