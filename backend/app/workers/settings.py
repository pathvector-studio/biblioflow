from typing import Any

from arq import cron
from arq.connections import RedisSettings
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import dispose_engine, get_engine
from app.workers.tasks import process_paper, scan_watch_folder


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


def watch_cron_schedule(poll_seconds: int) -> dict[str, Any]:
    """Map ``WATCH_POLL_SECONDS`` to an Arq cron schedule (cron has 1-second resolution).

    < 60s: every ``poll`` seconds within each minute (a non-divisor of 60 gives one shorter gap per minute);
    < 3600s: every ``poll // 60`` minutes at second 0; otherwise hourly.
    """
    if poll_seconds < 60:
        return {"second": set(range(0, 60, poll_seconds))}
    if poll_seconds < 3600:
        return {"minute": set(range(0, 60, poll_seconds // 60)), "second": {0}}
    return {"minute": {0}, "second": {0}}


class WorkerSettings:
    functions = [ping, process_paper]
    cron_jobs = [
        cron(scan_watch_folder, run_at_startup=True, unique=True, timeout=300, **watch_cron_schedule(get_settings().watch_poll_seconds))
    ]
    on_startup = on_startup
    on_shutdown = on_shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())
    max_jobs = 4
    job_timeout = 600
