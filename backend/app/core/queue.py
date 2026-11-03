"""Lazy Arq (Redis) pool used by the API to enqueue background jobs."""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings

from app.core.config import get_settings

logger = logging.getLogger("biblioflow.queue")

_pool: ArqRedis | None = None
_lock: asyncio.Lock | None = None


async def get_arq_pool() -> ArqRedis:
    global _pool, _lock
    if _pool is not None:
        return _pool
    if _lock is None:
        _lock = asyncio.Lock()
    async with _lock:
        if _pool is None:
            _pool = await create_pool(RedisSettings.from_dsn(get_settings().redis_url.get_secret_value()))
    return _pool


async def close_arq_pool() -> None:
    global _pool
    pool, _pool = _pool, None
    if pool is not None:
        await pool.aclose()


async def enqueue_process_paper(paper_id: uuid.UUID | str, pool: Any | None = None) -> bool:
    """Enqueue ``process_paper``; never raises (the paper simply stays ``index_status=pending``)."""
    try:
        pool = pool if pool is not None else await get_arq_pool()
        await pool.enqueue_job("process_paper", str(paper_id))
        return True
    except Exception:  # noqa: BLE001
        logger.warning("failed to enqueue process_paper for %s", paper_id, exc_info=True)
        return False
