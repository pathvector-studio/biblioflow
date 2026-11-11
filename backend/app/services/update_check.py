"""Opt-in update notice: look up the latest public BiblioFlow release (``UPDATE_CHECK=true``) and keep it in Valkey.

Nothing is sent but a plain HTTPS GET to GitHub's public "latest release" endpoint with a generic User-Agent: no
version, no instance identifier, no usage data. The request goes through ``safe_client``.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import UTC, datetime
from typing import Any

from redis.asyncio import Redis

from app import __version__
from app.core.net import safe_client, safe_get_json

logger = logging.getLogger(__name__)

RELEASE_API_URL = "https://api.github.com/repos/pathvector-studio/biblioflow/releases/latest"
RELEASE_URL_PREFIX = "https://github.com/pathvector-studio/biblioflow/"
ALLOWED_HOSTS = {"api.github.com"}
MAX_BYTES = 512 * 1024
TIMEOUT = 10.0
REDIS_KEY = "bf:update:latest"
TTL_SECONDS = 4 * 24 * 3600

_TAG = re.compile(r"^v(\d{1,6})\.(\d{1,6})\.(\d{1,6})$")
_SECURITY_HEADING = re.compile(r"^#{2,4}[ \t]+Security[ \t]*$", re.IGNORECASE | re.MULTILINE)


def parse_version(text: str) -> tuple[int, int, int] | None:
    """``1.2.3`` or ``v1.2.3`` -> (1, 2, 3); anything else (``-rc1``, ``latest``, ...) -> None."""
    m = _TAG.fullmatch(text if text.startswith("v") else "v" + text)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def parse_release(payload: Any, *, now: datetime | None = None) -> dict[str, Any] | None:
    """The fields we keep from GitHub's response, or None when it is not a plain ``vX.Y.Z`` release."""
    if not isinstance(payload, dict):
        return None
    tag = payload.get("tag_name")
    if not isinstance(tag, str) or parse_version(tag) is None or not tag.startswith("v"):
        return None
    url = payload.get("html_url")
    body = payload.get("body")
    published = payload.get("published_at")
    return {
        "version": tag[1:],
        "url": url if isinstance(url, str) and url.startswith(RELEASE_URL_PREFIX) else None,
        "published_at": published if isinstance(published, str) else None,
        "security": bool(isinstance(body, str) and _SECURITY_HEADING.search(body)),
        "checked_at": (now or datetime.now(UTC)).isoformat(),
    }


async def fetch_latest_release() -> dict[str, Any] | None:
    async with safe_client(
        allowed_hosts=ALLOWED_HOSTS,
        timeout=TIMEOUT,
        headers={"User-Agent": "BiblioFlow", "Accept": "application/vnd.github+json"},
    ) as client:
        payload = await safe_get_json(client, RELEASE_API_URL, max_bytes=MAX_BYTES)
    return parse_release(payload)


async def run_update_check(redis: Redis, *, enabled: bool) -> bool:
    """Fetch and store the latest release. Does nothing (no request at all) unless ``enabled``.

    Returns True when a value was stored. Any failure is logged and leaves the previous value in place.
    """
    if not enabled:
        return False
    try:
        info = await fetch_latest_release()
        if info is None:
            logger.warning("update check: latest release has no plain vX.Y.Z tag; ignored")
            return False
        await redis.set(REDIS_KEY, json.dumps(info), ex=TTL_SECONDS)
        return True
    except Exception as exc:  # noqa: BLE001 - a failed check must never break the worker
        logger.warning("update check failed: %s", type(exc).__name__)
        return False


async def read_status(redis: Redis, *, enabled: bool) -> dict[str, Any]:
    """The body of ``GET /system/update-status`` (reads Valkey only; never the network)."""
    status: dict[str, Any] = {
        "enabled": enabled,
        "current": __version__,
        "latest": None,
        "update_available": False,
        "security": False,
        "url": None,
        "checked_at": None,
    }
    if not enabled:
        return status
    raw = await redis.get(REDIS_KEY)
    if not raw:
        return status
    try:
        info = json.loads(raw)
        latest = parse_version(info["version"])
        current = parse_version(__version__)
    except (ValueError, KeyError, TypeError):
        return status
    if latest is None:
        return status
    available = current is not None and latest > current
    status.update(
        latest=info["version"],
        update_available=available,
        security=bool(info.get("security")) and available,
        url=info.get("url"),
        checked_at=info.get("checked_at"),
    )
    return status
