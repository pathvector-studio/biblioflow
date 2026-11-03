"""Shared fetch helpers for metadata clients: map SSRF blocks and HTTP errors to ``UpstreamError``."""

from __future__ import annotations

from typing import Any

import httpx

from app.core.net import BlockedDestination, UpstreamHTTPError, safe_get_bytes, safe_get_json
from app.schemas.metadata import UpstreamError

MAX_RESPONSE_BYTES = 2 * 1024 * 1024


async def fetch_json(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    not_found_ok: bool = False,
) -> Any | None:
    try:
        return await safe_get_json(
            client, url, max_bytes=MAX_RESPONSE_BYTES, headers=headers, params=params
        )
    except UpstreamHTTPError as exc:
        if not_found_ok and exc.status_code == 404:
            return None
        raise
    except BlockedDestination as exc:
        raise UpstreamError(str(exc)) from exc


async def fetch_bytes(
    client: httpx.AsyncClient, url: str, *, params: dict[str, Any] | None = None
) -> bytes:
    try:
        _, body = await safe_get_bytes(client, url, max_bytes=MAX_RESPONSE_BYTES, params=params)
    except BlockedDestination as exc:
        raise UpstreamError(str(exc)) from exc
    return body
