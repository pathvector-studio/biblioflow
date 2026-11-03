"""SSRF defence: destination validation, a connect-time IP-checking network backend, safe httpx clients."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import socket
import ssl
import typing
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpcore
import httpx

from app.core.config import get_settings
from app.schemas.metadata import UpstreamError



class UpstreamHTTPError(UpstreamError):
    """Non-2xx upstream response; ``status_code`` lets callers treat 404 as "not found"."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"upstream returned HTTP {status_code}")
        self.status_code = status_code


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address

ALLOWED_PORTS = frozenset({80, 443})
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
USER_AGENT = "BiblioFlow/0.2 (+https://github.com/pathvector-studio/biblioflow)"

_BLOCKED_NETWORKS = tuple(
    ipaddress.ip_network(n)
    for n in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "::1/128",
        "fc00::/7",
        "fe80::/10",
    )
)
_NAT64 = ipaddress.ip_network("64:ff9b::/96")


class BlockedDestination(Exception):
    """The destination (scheme, port, host or resolved IP) is not allowed."""


def _unwrap(ip: IPAddress) -> IPAddress:
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            return ip.ipv4_mapped
        if ip.sixtofour is not None:
            return ip.sixtofour
        if ip in _NAT64:
            return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return ip


def check_ip(ip: IPAddress) -> None:
    """Raise ``BlockedDestination`` unless ``ip`` is a public, globally routable address."""
    ip = _unwrap(ip)
    if (
        not ip.is_global
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or any(ip in net for net in _BLOCKED_NETWORKS if net.version == ip.version)
    ):
        raise BlockedDestination(f"destination address {ip} is not allowed")


def _parse_ip_literal(host: str) -> IPAddress | None:
    try:
        return ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return None


def validate_url(url: str | httpx.URL, allowed_hosts: set[str] | None = None) -> httpx.URL:
    """Validate scheme/port/userinfo/host (and IP literals); return the parsed URL."""
    try:
        u = httpx.URL(url) if isinstance(url, str) else url
    except httpx.InvalidURL as exc:
        raise BlockedDestination("invalid URL") from exc
    if u.scheme not in ("http", "https"):
        raise BlockedDestination(f"scheme {u.scheme!r} is not allowed")
    if u.userinfo:
        raise BlockedDestination("credentials in URL are not allowed")
    host = u.host
    if not host:
        raise BlockedDestination("URL has no host")
    if u.port is not None and u.port not in ALLOWED_PORTS:
        raise BlockedDestination(f"port {u.port} is not allowed")
    ip = _parse_ip_literal(host)
    if ip is not None:
        check_ip(ip)
    if allowed_hosts is not None and host.lower() not in allowed_hosts:
        raise BlockedDestination(f"host {host!r} is not in the allowlist")
    return u


class SafeNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolves, validates every address, then connects to the validated IP (no re-resolution)."""

    def __init__(self) -> None:
        self._inner = httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        literal = _parse_ip_literal(host)
        if literal is not None:
            addrs = [literal]
        else:
            try:
                infos = await asyncio.get_running_loop().getaddrinfo(
                    host, port, type=socket.SOCK_STREAM
                )
            except OSError as exc:
                raise httpcore.ConnectError(f"cannot resolve {host}") from exc
            addrs = []
            for info in infos:
                addr = ipaddress.ip_address(info[4][0].split("%", 1)[0])
                if addr not in addrs:
                    addrs.append(addr)
            if not addrs:
                raise httpcore.ConnectError(f"cannot resolve {host}")
        for addr in addrs:
            check_ip(addr)
        last_exc: httpcore.ConnectError | None = None
        for addr in addrs:  # every address is validated; fall back across them like a normal resolver would
            try:
                return await self._inner.connect_tcp(
                    str(addr),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except httpcore.ConnectError as exc:
                last_exc = exc
        assert last_exc is not None
        raise last_exc

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: typing.Iterable[typing.Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise BlockedDestination("unix sockets are not allowed")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class SafeTransport(httpx.AsyncHTTPTransport):
    """``AsyncHTTPTransport`` whose connection pool uses :class:`SafeNetworkBackend`.

    TLS SNI and certificate verification use the request origin hostname (httpcore passes it
    as ``server_hostname``), so connecting to the validated IP is safe.
    """

    def __init__(self) -> None:
        ctx = httpx.create_ssl_context(verify=True, trust_env=False)
        super().__init__(verify=ctx, trust_env=False)
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ctx,
            max_connections=20,
            max_keepalive_connections=10,
            keepalive_expiry=30.0,
            network_backend=SafeNetworkBackend(),
        )


class SafeClient(httpx.AsyncClient):
    allowed_hosts: set[str] | None = None


def safe_client(
    *,
    allowed_hosts: set[str] | None = None,
    timeout: float | None = None,
    headers: dict[str, str] | None = None,
) -> SafeClient:
    settings = get_settings()
    ua = USER_AGENT + (f" mailto:{settings.crossref_mailto}" if settings.crossref_mailto else "")
    client = SafeClient(
        transport=SafeTransport(),
        trust_env=False,
        follow_redirects=False,
        timeout=timeout if timeout is not None else settings.external_http_timeout,
        headers={"User-Agent": ua, **(headers or {})},
    )
    client.allowed_hosts = {h.lower() for h in allowed_hosts} if allowed_hosts else None
    return client


def _hosts(client: httpx.AsyncClient, allowed_hosts: set[str] | None) -> set[str] | None:
    return allowed_hosts if allowed_hosts is not None else getattr(client, "allowed_hosts", None)


@asynccontextmanager
async def safe_stream(
    client: httpx.AsyncClient,
    url: str | httpx.URL,
    *,
    method: str = "GET",
    allowed_hosts: set[str] | None = None,
    max_redirects: int = 3,
    headers: dict[str, str] | None = None,
) -> AsyncIterator[httpx.Response]:
    """Stream ``url`` following redirects manually, re-validating every hop."""
    hosts = _hosts(client, allowed_hosts)
    current = validate_url(url, hosts)
    response: httpx.Response | None = None
    try:
        for hop in range(max_redirects + 1):
            request = client.build_request(method, current, headers=headers)
            response = await client.send(request, stream=True, follow_redirects=False)
            if response.status_code not in REDIRECT_STATUSES:
                break
            location = response.headers.get("location")
            await response.aclose()
            response = None
            if not location:
                raise BlockedDestination("redirect without Location")
            if hop >= max_redirects:
                raise BlockedDestination("too many redirects")
            try:
                nxt = current.join(location)
            except httpx.InvalidURL as exc:
                raise BlockedDestination("invalid redirect target") from exc
            current = validate_url(nxt, hosts)
            if response is None and method == "POST":
                method = "GET"
        assert response is not None
        yield response
    finally:
        if response is not None:
            await response.aclose()


async def safe_get_bytes(
    client: httpx.AsyncClient,
    url: str | httpx.URL,
    *,
    max_bytes: int,
    allowed_hosts: set[str] | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, typing.Any] | None = None,
) -> tuple[httpx.Response, bytes]:
    """GET with a body size cap. Raises ``BlockedDestination`` or ``UpstreamError``."""
    try:
        target = httpx.URL(url) if isinstance(url, str) else url
        if params:
            target = target.copy_merge_params(params)
    except httpx.InvalidURL as exc:
        raise BlockedDestination("invalid URL") from exc
    try:
        async with safe_stream(
            client, target, allowed_hosts=allowed_hosts, headers=headers
        ) as response:
            if response.status_code // 100 != 2:
                raise UpstreamHTTPError(response.status_code)
            declared = response.headers.get("content-length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise UpstreamError("upstream response too large")
            buf = bytearray()
            async for chunk in response.aiter_bytes():
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    raise UpstreamError("upstream response too large")
            return response, bytes(buf)
    except httpx.HTTPError as exc:
        raise UpstreamError(f"upstream request failed: {type(exc).__name__}") from exc


async def safe_get_json(
    client: httpx.AsyncClient,
    url: str | httpx.URL,
    *,
    max_bytes: int,
    allowed_hosts: set[str] | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, typing.Any] | None = None,
) -> typing.Any:
    _, body = await safe_get_bytes(
        client,
        url,
        max_bytes=max_bytes,
        allowed_hosts=allowed_hosts,
        headers=headers,
        params=params,
    )
    try:
        return json.loads(body)
    except ValueError as exc:
        raise UpstreamError("upstream returned invalid JSON") from exc
