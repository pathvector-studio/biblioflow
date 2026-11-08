"""Import from the server folder as a background job (spec R5, R7, R8).

Phase 1 reads the chosen bibliography file from ``{IMPORT_DIR}/{workspace_slug}/`` and applies it with
``import_apply.run_import`` (one transaction). Phase 2 goes through the records that have file attachments
and, for each paper that has no PDF yet, stores the first usable PDF through ``ingest.ingest_pdf`` (the same
checks as an upload: magic bytes, size, isolated parser). A paper that already has a PDF is never touched, so
running the same import again changes nothing. A bad attachment fails that record's PDF only.
"""

from __future__ import annotations

import asyncio
import logging
import os
import stat
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.import_job import ACTIVE_STATUSES, ImportJob
from app.models.paper import Paper
from app.models.workspace import Workspace
from app.schemas.metadata import MetadataResolver
from app.services import import_apply
from app.services.import_folder import UnsafePath, resolve_in_folder, workspace_folder
from app.services.importers import ImportFormatError, parse_file
from app.services.ingest import IngestError, ingest_pdf, safe_original_name
from app.services.pdf import InvalidPdf
from app.services.storage.files import TooLarge, receive_stream

logger = logging.getLogger("biblioflow.import_job")

MAX_SOURCE_BYTES = 64 * 1024 * 1024  # the bibliography file itself (the upload route allows 4 MiB)
MAX_RECORDS = 10_000
MAX_PROBLEMS = 100
STALE_AFTER = timedelta(minutes=30)  # a queued / running job with no heartbeat this long is dead
READ_CHUNK = 64 * 1024


def job_id_for(job_id: uuid.UUID | str) -> str:
    return f"import:{job_id}"


async def expire_stale_jobs(db: AsyncSession, workspace_id: uuid.UUID) -> None:
    """Mark jobs whose worker died as failed, so they do not block the workspace for good."""
    now = datetime.now(UTC)
    await db.execute(
        update(ImportJob)
        .where(
            ImportJob.workspace_id == workspace_id,
            ImportJob.status.in_(ACTIVE_STATUSES),
            ImportJob.updated_at < now - STALE_AFTER,
        )
        .values(status="failed", error="The worker stopped before the import finished.", finished_at=now, updated_at=now)
    )


def _read_regular_file(path: Path, limit: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = os.open(path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise UnsafePath("not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            return fh.read(limit + 1)
    finally:
        os.close(fd)


async def _file_chunks(path: Path) -> AsyncIterator[bytes]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    fd = await asyncio.to_thread(os.open, path, flags)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise UnsafePath("not a regular file")
        with os.fdopen(fd, "rb", closefd=False) as fh:
            while chunk := await asyncio.to_thread(fh.read, READ_CHUNK):
                yield chunk
    finally:
        os.close(fd)


def _short(text: str, n: int = 160) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


async def _touch(db: AsyncSession, job: ImportJob, **values: Any) -> None:
    for k, v in values.items():
        setattr(job, k, v)
    job.updated_at = datetime.now(UTC)  # heartbeat, even when nothing else changed
    await db.commit()


def _add_problem(job: ImportJob, ref: str, reason: str, stage: str) -> None:
    if len(job.problems) < MAX_PROBLEMS:
        job.problems = [*job.problems, {"ref": _short(ref, 200), "reason": _short(reason, 300), "stage": stage}]


async def _attach_pdf(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    paper_id: uuid.UUID,
    attachments: list[str],
    folder: Path,
    base: str,
    library_root: Path,
    max_bytes: int,
    resolver: MetadataResolver,
    enqueue_pool: Any,
) -> tuple[str, str | None]:
    """``("attached" | "skipped" | "failed", reason)`` for one paper. Tries the attachments in order."""
    async with sessionmaker() as db:
        paper = await db.get(Paper, paper_id)
        workspace = await db.get(Workspace, workspace_id)
        if paper is None or workspace is None or paper.workspace_id != workspace_id or paper.has_pdf:
            return "skipped", None
        reasons: list[str] = []
        for raw in attachments:
            label = _short(raw.strip(), 120)
            try:
                path, rel = resolve_in_folder(folder, raw, base=base)
            except UnsafePath as exc:
                reasons.append(f"{label}: {exc}")
                continue
            if not rel.lower().endswith(".pdf"):
                continue  # a snapshot or another kind of attachment: not a PDF to store
            try:
                staged = await receive_stream(_file_chunks(path), library_root, max_bytes)
                await ingest_pdf(
                    db, staged=staged, workspace=workspace, user_id=user_id, resolver=resolver,
                    original_filename=safe_original_name(path.name), paper_id=paper_id, redis_pool_for_enqueue=enqueue_pool,
                )
            except IngestError as exc:
                if exc.status_code == 409:  # got a PDF in the meantime
                    return "skipped", None
                reasons.append(f"{label}: {exc.detail}")
            except TooLarge:
                reasons.append(f"{label}: file too large")
            except (UnsafePath, InvalidPdf, OSError) as exc:
                reasons.append(f"{label}: {exc if isinstance(exc, (UnsafePath, InvalidPdf)) else 'cannot be read'}")
            except Exception:  # noqa: BLE001 - one record never stops the import
                logger.exception("unexpected error attaching a PDF")
                reasons.append(f"{label}: unexpected error")
            else:
                return "attached", None
        return ("failed", reasons[0]) if reasons else ("skipped", None)


async def run_import_job(
    job_id: uuid.UUID,
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    import_dir: Path,
    library_root: Path,
    max_bytes: int,
    resolver: MetadataResolver,
    enqueue_pool: Any,
) -> str:
    """Run one queued job to the end. Returns the final status (``"skipped"`` if it was not queued)."""
    async with sessionmaker() as jdb:
        job = await jdb.get(ImportJob, job_id)
        if job is None or job.status != "queued":
            return "skipped"
        workspace = await jdb.get(Workspace, job.workspace_id)
        if workspace is None:
            return "skipped"
        workspace_id, slug, owner_id = workspace.id, workspace.slug, workspace.owner_id
        user_id = job.user_id or owner_id
        await _touch(jdb, job, status="running", started_at=datetime.now(UTC))
        try:
            await _run(
                jdb, job, workspace_id=workspace_id, user_id=user_id, folder=workspace_folder(import_dir, slug),
                sessionmaker=sessionmaker, library_root=library_root, max_bytes=max_bytes, resolver=resolver,
                enqueue_pool=enqueue_pool,
            )
            await _touch(jdb, job, status="done", finished_at=datetime.now(UTC))
        except _JobFailed as exc:
            await _touch(jdb, job, status="failed", error=str(exc), finished_at=datetime.now(UTC))
        except Exception as exc:  # noqa: BLE001
            logger.exception("import job %s failed", job_id)
            await jdb.rollback()
            await jdb.refresh(job)
            await _touch(
                jdb, job, status="failed", error=f"Import failed ({type(exc).__name__})", finished_at=datetime.now(UTC)
            )
        return job.status


class _JobFailed(Exception):
    """A reason that is safe to show to the user."""


async def _run(
    jdb: AsyncSession,
    job: ImportJob,
    *,
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    folder: Path,
    sessionmaker: async_sessionmaker[AsyncSession],
    library_root: Path,
    max_bytes: int,
    resolver: MetadataResolver,
    enqueue_pool: Any,
) -> None:
    try:
        path, rel = resolve_in_folder(folder, job.source)
        data = await asyncio.to_thread(_read_regular_file, path, MAX_SOURCE_BYTES)
    except (UnsafePath, OSError) as exc:
        raise _JobFailed(f"Cannot read {_short(job.source, 100)}: {exc if isinstance(exc, UnsafePath) else 'read error'}") from None
    if len(data) > MAX_SOURCE_BYTES:
        raise _JobFailed("The bibliography file is too large")
    if not data.strip():
        raise _JobFailed("The bibliography file is empty")
    try:
        parsed = await asyncio.to_thread(parse_file, data, path.name)
    except ImportFormatError as exc:
        raise _JobFailed(str(exc)) from None
    if len(parsed.items) + len(parsed.problems) > MAX_RECORDS:
        raise _JobFailed(f"Too many records (at most {MAX_RECORDS} per file)")
    base = "/".join(rel.split("/")[:-1])

    async with sessionmaker() as mdb:
        workspace = await mdb.get(Workspace, workspace_id)
        assert workspace is not None
        outcome = await import_apply.run_import(mdb, workspace=workspace, user_id=user_id, parsed=parsed, dry_run=False)
        await mdb.commit()

    with_pdfs = [r for r in outcome.results if r.paper_id is not None and r.attachments]
    for r in outcome.results:
        if r.status == "unreadable":
            _add_problem(job, r.ref, r.reason or "could not be read", "record")
    await _touch(
        jdb, job, total=len(outcome.results), new=outcome.count("new"), matched=outcome.count("matched"),
        unreadable=outcome.count("unreadable"), with_attachments=len(with_pdfs),
    )

    for r in with_pdfs:
        assert r.paper_id is not None
        outcome_kind, reason = await _attach_pdf(
            sessionmaker=sessionmaker, workspace_id=workspace_id, user_id=user_id, paper_id=r.paper_id,
            attachments=r.attachments, folder=folder, base=base, library_root=library_root, max_bytes=max_bytes,
            resolver=resolver, enqueue_pool=enqueue_pool,
        )
        values: dict[str, Any] = {"pdfs_processed": job.pdfs_processed + 1}
        if outcome_kind == "attached":
            values["pdfs_attached"] = job.pdfs_attached + 1
        elif outcome_kind == "failed":
            values["pdfs_failed"] = job.pdfs_failed + 1
            _add_problem(job, r.ref, reason or "PDF could not be stored", "pdf")
        await _touch(jdb, job, **values)


async def active_job(db: AsyncSession, workspace_id: uuid.UUID) -> ImportJob | None:
    return (
        await db.execute(
            select(ImportJob).where(ImportJob.workspace_id == workspace_id, ImportJob.status.in_(ACTIVE_STATUSES))
        )
    ).scalar_one_or_none()
