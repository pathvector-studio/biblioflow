"""Redis fixed-window rate limiter (INCR + EXPIRE)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from redis.asyncio import Redis

from app.core.errors import RateLimitExceeded


@dataclass(frozen=True)
class RateLimitResult:
    allowed: bool
    count: int
    limit: int
    retry_after: int


class RateLimiter:
    def __init__(self, redis: Redis, key: str, limit: int, window: int) -> None:
        self.redis = redis
        self.key = key
        self.limit = limit
        self.window = window

    async def hit(self) -> RateLimitResult:
        async with self.redis.pipeline(transaction=True) as pipe:
            pipe.incr(self.key)
            pipe.ttl(self.key)
            count, ttl = await pipe.execute()
        if ttl is None or ttl < 0:  # first hit, or EXPIRE was lost
            await self.redis.expire(self.key, self.window)
            ttl = self.window
        return RateLimitResult(count <= self.limit, int(count), self.limit, int(ttl))


def make_key(name: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()[:32]
    return f"bf:rl:{name}:{digest}"


async def enforce_rate_limit(redis: Redis, name: str, parts: tuple[str, ...], limit: int, window: int = 60) -> None:
    """Raise ``RateLimitExceeded`` (429 + Retry-After) when the window budget is exhausted."""
    result = await RateLimiter(redis, make_key(name, *parts), limit, window).hit()
    if not result.allowed:
        raise RateLimitExceeded(result.retry_after)
