"""Background tasks: post-ingest processing and the watch-folder scanner."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import stat
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models.paper import IndexStatus, Paper
from app.models.workspace import Workspace
from app.schemas.metadata import MetadataResolver
from app.services.ingest import IngestError, ingest_pdf, safe_original_name
from app.services.metadata import get_resolver
from app.services.pdf import InvalidPdf, inspect_pdf
from app.services.storage.files import TooLarge, receive_stream, sha256_file
from app.services.storage.paths import PathTraversalError, resolve_in_library

logger = logging.getLogger("biblioflow.worker")

REJECTED_DIR = "_rejected"
ROOT_BUCKET = "_root"
STABLE_SECONDS = 5.0
LOCK_TTL = 600
SEEN_KEY = "bf:watch:seen"
LOCK_PREFIX = "bf:watch:lock:"
ATTEMPT_PREFIX = "bf:watch:attempts:"
ATTEMPT_TTL = 24 * 3600
MAX_ATTEMPTS = 5
READ_CHUNK = 64 * 1024
_SAFE_COMPONENT = re.compile(r"[^A-Za-z0-9._-]+")


# ------------------------------------------------------------------ process_paper
async def process_paper(ctx: dict[str, Any], paper_id: str) -> str:
    """Verify the stored file and fix up ``page_count``. Phase 3 adds chunking/embedding here."""
    sessionmaker = get_sessionmaker()
    settings = get_settings()
    async with sessionmaker() as db:
        paper = await db.get(Paper, uuid.UUID(paper_id))
        if paper is None or not paper.has_pdf or not paper.relative_file_path:
            return "skipped"
        try:
            path = resolve_in_library(paper.relative_file_path, settings.library_root)
            if not path.is_file() or path.is_symlink():
                raise FileNotFoundError(path)
            size = path.stat().st_size
            sha = await asyncio.to_thread(sha256_file, path)
            if sha != paper.file_sha256 or size != paper.file_size:
                logger.error("file for paper %s changed on disk", paper_id)
                paper.index_status = IndexStatus.failed
                await db.commit()
                return "failed"
            info = await asyncio.to_thread(inspect_pdf, path)
        except (PathTraversalError, OSError, InvalidPdf):
            logger.exception("process_paper failed for %s", paper_id)
            paper.index_status = IndexStatus.failed
            await db.commit()
            return "failed"
        paper.page_count = info.page_count
        await db.commit()
        return "ok"


# ------------------------------------------------------------------ watch folder
@dataclass
class ScanResult:
    ingested: list[str] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    retried: list[str] = field(default_factory=list)


def _component(name: str, default: str) -> str:
    """Filesystem-safe single path component (basename only, no leading dots)."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1]
    base = _SAFE_COMPONENT.sub("_", base).lstrip(".")[:200]
    return base or default


def _reject_file(watch_dir: Path, bucket: str, src: Path, reason: str) -> Path:
    dest_dir = watch_dir / REJECTED_DIR / _component(bucket, "_unknown")
    dest_dir.mkdir(parents=True, exist_ok=True)
    name = _component(src.name, "file.pdf")
    stem, suffix = os.path.splitext(name)
    dest = dest_dir / name
    n = 1
    while dest.exists() or dest.is_symlink() or Path(f"{dest}.error.txt").exists():
        n += 1
        dest = dest_dir / f"{stem}_{n}{suffix}"
    shutil.move(str(src), str(dest))
    Path(f"{dest}.error.txt").write_text(reason.strip() + "\n", encoding="utf-8")
    return dest


async def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = await asyncio.to_thread(os.open, path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise IngestError(422, "not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            while chunk := await asyncio.to_thread(fh.read, READ_CHUNK):
                yield chunk
    finally:
        os.close(fd)


def _dec(v: Any) -> str | None:
    return v.decode() if isinstance(v, bytes) else v


async def _ingest_one(
    path: Path,
    slug: str,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    library_root: Path,
    max_bytes: int,
    resolver: MetadataResolver,
    enqueue_pool: Any,
) -> None:
    async with sessionmaker() as db:
        ws = (await db.execute(select(Workspace).where(Workspace.slug == slug))).scalar_one_or_none()
        if ws is None:
            raise IngestError(404, f"No workspace with slug {slug!r}")
        staged = await receive_stream(_file_chunks(path), library_root, max_bytes)
        await ingest_pdf(
            db,
            staged=staged,
            workspace=ws,
            user_id=ws.owner_id,
            resolver=resolver,
            original_filename=safe_original_name(path.name),
            redis_pool_for_enqueue=enqueue_pool,
        )


def _list_candidates(watch_dir: Path) -> list[tuple[str, Path, os.stat_result]]:
    """Regular, non-hidden ``*.pdf`` files: directly under ``watch_dir`` (bucket ``_root``) or one level down.

    Symlinks (files or directories) are never followed.
    """
    out: list[tuple[str, Path, os.stat_result]] = []
    try:
        entries = sorted(os.scandir(watch_dir), key=lambda e: e.name)
    except FileNotFoundError:
        return out

    def consider(bucket: str, entry: os.DirEntry[str]) -> None:
        if entry.name.startswith(".") or not entry.name.lower().endswith(".pdf"):
            return
        try:
            st = os.lstat(entry.path)
        except OSError:
            return
        if stat.S_ISREG(st.st_mode):
            out.append((bucket, Path(entry.path), st))

    for entry in entries:
        if entry.name.startswith("."):
            continue
        if entry.is_symlink():
            continue
        if entry.is_file(follow_symlinks=False):
            consider(ROOT_BUCKET, entry)
        elif entry.is_dir(follow_symlinks=False) and entry.name != REJECTED_DIR:
            try:
                children = sorted(os.scandir(entry.path), key=lambda e: e.name)
            except OSError:
                continue
            for child in children:
                if child.is_file(follow_symlinks=False) and not child.is_symlink():
                    consider(entry.name, child)
    return out


def _is_permanent(exc: Exception) -> bool:
    """Validation-type failures never succeed on retry; anything else (DB/IO/unexpected, 5xx) may."""
    if isinstance(exc, IngestError):
        return exc.status_code < 500
    return isinstance(exc, (InvalidPdf, TooLarge, PathTraversalError))


def _reason(exc: Exception) -> str:
    reason = exc.detail if isinstance(exc, IngestError) else f"{type(exc).__name__}: {exc}"
    if isinstance(exc, IngestError) and exc.extra.get("paper_id"):
        reason += f" (paper_id={exc.extra['paper_id']})"
    return reason


async def _reject(watch_dir: Path, bucket: str, path: Path, reason: str, key: str, result: "ScanResult") -> None:
    dest = await asyncio.to_thread(_reject_file, watch_dir, bucket, path, reason)
    logger.warning("watch: rejected %s -> %s (%s)", path, dest, reason)
    result.rejected.append(key)


async def scan_watch_folder_core(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    redis: Any,
    watch_dir: Path,
    library_root: Path,
    max_bytes: int,
    resolver: MetadataResolver,
    enqueue_pool: Any,
    now: Callable[[], float] = time.time,
    stable_seconds: float = STABLE_SECONDS,
) -> ScanResult:
    """One scan pass. A file is processed only when its (size, mtime) is unchanged since the previous
    pass and its mtime is at least ``stable_seconds`` old. One file's failure never stops the pass."""
    result = ScanResult()
    watch_dir = Path(watch_dir)
    candidates = await asyncio.to_thread(_list_candidates, watch_dir)
    for bucket, path, st in candidates:
        key = str(path)
        sig = f"{st.st_size}:{st.st_mtime_ns}"
        try:
            prev = _dec(await redis.hget(SEEN_KEY, key))
            await redis.hset(SEEN_KEY, key, sig)
            if prev != sig or now() - st.st_mtime < stable_seconds:
                result.skipped.append(key)
                continue
            lock = LOCK_PREFIX + hashlib.sha256(key.encode()).hexdigest()
            if not await redis.set(lock, "1", nx=True, ex=LOCK_TTL):
                result.skipped.append(key)
                continue
            try:
                attempt_key = ATTEMPT_PREFIX + hashlib.sha256(key.encode()).hexdigest()
                try:
                    if bucket == ROOT_BUCKET:
                        raise IngestError(422, "Files must be placed in a workspace subdirectory (watch/<workspace-slug>/)")
                    await _ingest_one(
                        path, bucket, sessionmaker=sessionmaker, library_root=library_root, max_bytes=max_bytes,
                        resolver=resolver, enqueue_pool=enqueue_pool,
                    )
                except Exception as exc:  # noqa: BLE001 - classified below
                    reason = _reason(exc)
                    if _is_permanent(exc):
                        await redis.delete(attempt_key)
                        await _reject(watch_dir, bucket, path, reason, key, result)
                    else:
                        attempts = await redis.incr(attempt_key)
                        await redis.expire(attempt_key, ATTEMPT_TTL)
                        if attempts >= MAX_ATTEMPTS:
                            await redis.delete(attempt_key)
                            await _reject(
                                watch_dir, bucket, path, f"{reason} (gave up after {attempts} attempts)", key, result
                            )
                        else:
                            logger.warning("watch: transient failure %d/%d for %s: %s", attempts, MAX_ATTEMPTS, path, reason)
                            result.retried.append(key)
                else:
                    await redis.delete(attempt_key)
                    try:
                        await asyncio.to_thread(path.unlink)
                    except OSError:
                        logger.exception("watch: ingested but could not remove %s", path)
                    result.ingested.append(key)
                await redis.hdel(SEEN_KEY, key)  # transient failures are re-evaluated from scratch next pass
            finally:
                await redis.delete(lock)
        except Exception:  # noqa: BLE001 - isolate failures per file
            logger.exception("watch: unexpected error handling %s", path)
    return result


async def scan_watch_folder(ctx: dict[str, Any]) -> dict[str, int]:
    settings = get_settings()
    res = await scan_watch_folder_core(
        sessionmaker=get_sessionmaker(),
        redis=ctx["redis"],
        watch_dir=settings.watch_dir,
        library_root=settings.library_root,
        max_bytes=settings.max_upload_bytes,
        resolver=get_resolver(),
        enqueue_pool=ctx["redis"],
    )
    return {"ingested": len(res.ingested), "rejected": len(res.rejected), "skipped": len(res.skipped)}
