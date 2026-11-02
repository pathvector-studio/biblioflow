from typing import Any

from arq.connections import RedisSettings
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import dispose_engine, get_engine


async def ping(ctx: dict[str, Any]) -> str:
    """Health task: verifies the worker can reach the database."""
    async with get_engine().connect() as conn:
        await conn.execute(text("SELECT 1"))
    return "pong"


async def on_startup(ctx: dict[str, Any]) -> None:
    async with get_engine().connect() as conn:  # fail fast if DB is unreachable
        await conn.execute(text("SELECT 1"))


async def on_shutdown(ctx: dict[str, Any]) -> None:
    await dispose_engine()


class WorkerSettings:
    functions = [ping]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())
    max_jobs = 4
    job_timeout = 600
