"""Auto-export: keep ``{EXPORT_DIR}/{workspace_slug}/{filename}`` in step with the library (worker side)."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Text, func, literal_column, select
from sqlalchemy.dialects.postgresql import aggregate_order_by
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.auto_export import AutoExport
from app.models.paper import Paper, PaperStatus
from app.models.workspace import Workspace
from app.services import export as export_svc

logger = logging.getLogger(__name__)

MAX_AUTO_EXPORT_PAPERS = 20000
MAX_PER_WORKSPACE = 20
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")
EXTENSIONS = {fmt: ext for fmt, (_, ext) in export_svc.FORMATS.items()}


class ExportPathError(ValueError):
    """The output path would leave the export directory (or the name is not allowed)."""


def validate_filename(filename: str, fmt: str) -> str:
    """``[A-Za-z0-9._-]`` only, no leading dot, no ``..``, and the extension of the format."""
    if fmt not in EXTENSIONS:
        raise ExportPathError("unknown format")
    if not isinstance(filename, str) or not FILENAME_RE.fullmatch(filename) or ".." in filename:
        raise ExportPathError("filename may contain only letters, digits, '.', '_' and '-'")
    if not filename.endswith("." + EXTENSIONS[fmt]):
        raise ExportPathError(f"filename must end with .{EXTENSIONS[fmt]}")
    return filename


def resolve_export_path(export_root: Path, workspace_slug: str, filename: str) -> Path:
    """``export_root/slug/filename``, asserted (after ``resolve()``, so symlinks count) to stay under ``export_root``."""
    if not FILENAME_RE.fullmatch(workspace_slug or "") or ".." in workspace_slug:
        raise ExportPathError("invalid workspace slug")
    if not FILENAME_RE.fullmatch(filename or "") or ".." in filename:
        raise ExportPathError("invalid filename")
    root = Path(export_root).resolve()
    target = (root / workspace_slug / filename).resolve()
    if not target.is_relative_to(root) or target.parent.parent != root:
        raise ExportPathError("path escapes the export directory")
    return target


def write_atomic(target: Path, data: bytes) -> None:
    """Write next to ``target`` and ``os.replace`` it in, so readers never see a partial file."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def delete_export_file(export_root: Path, workspace_slug: str, filename: str) -> None:
    try:
        resolve_export_path(export_root, workspace_slug, filename).unlink(missing_ok=True)
    except (ExportPathError, OSError):
        logger.warning("auto-export: could not remove %s/%s", workspace_slug, filename)


def _where(ae: AutoExport) -> list:
    from app.api.v1.papers import filter_conditions

    return [
        Paper.workspace_id == ae.workspace_id,
        *filter_conditions(
            q=ae.q,
            tag=ae.tag,
            collection_id=ae.collection_id,
            year=ae.year,
            status=PaperStatus(ae.status) if ae.status else None,
        ),
    ]


async def fingerprint(db: AsyncSession, ae: AutoExport) -> str:
    """Cheap digest of what the export would contain: count, newest ``updated_at`` and a hash of the ids.
    Adds, deletes, edits, and tag/collection changes that move a paper in or out of the filter all change it."""
    ids_hash = func.md5(
        func.coalesce(func.string_agg(Paper.id.cast(Text), aggregate_order_by(literal_column("','"), Paper.id)), "")
    )
    count, newest, h = (
        await db.execute(select(func.count(), func.max(Paper.updated_at), ids_hash).where(*_where(ae)))
    ).one()
    return f"{count}:{newest.isoformat() if newest else '-'}:{h}"


async def render_for(db: AsyncSession, ae: AutoExport) -> tuple[str, int]:
    """The file content: identical to ``GET /export`` with the same filters."""
    papers = (
        (
            await db.execute(
                select(Paper).where(*_where(ae)).order_by(Paper.created_at, Paper.id).limit(MAX_AUTO_EXPORT_PAPERS)
            )
        )
        .scalars()
        .all()
    )
    return export_svc.render(ae.format, [export_svc.record_from_paper(p) for p in papers]), len(papers)


@dataclass
class SyncResult:
    written: list[uuid.UUID]
    skipped: list[uuid.UUID]
    failed: list[uuid.UUID]


async def sync_auto_exports(sessionmaker: async_sessionmaker[AsyncSession], export_root: Path) -> SyncResult:
    """One pass over every definition. A failure is stored in ``last_error`` and never stops the pass."""
    res = SyncResult([], [], [])
    async with sessionmaker() as db:
        rows = (
            await db.execute(
                select(AutoExport.id, Workspace.slug).join(Workspace, Workspace.id == AutoExport.workspace_id)
            )
        ).all()
    for ae_id, slug in rows:
        try:
            async with sessionmaker() as db:
                ae = await db.get(AutoExport, ae_id)
                if ae is None:
                    continue
                target = resolve_export_path(export_root, slug, ae.filename)
                fp = await fingerprint(db, ae)
                if ae.fingerprint == fp and ae.last_error is None and await asyncio.to_thread(target.is_file):
                    res.skipped.append(ae_id)
                    continue
                body, count = await render_for(db, ae)
                await asyncio.to_thread(write_atomic, target, body.encode("utf-8"))
                ae.fingerprint = fp
                ae.paper_count = count
                ae.last_written_at = datetime.now(UTC)
                ae.last_error = None
                await db.commit()
                res.written.append(ae_id)
        except Exception as exc:  # noqa: BLE001 - recorded; the other definitions still run
            logger.warning("auto-export %s failed: %s", ae_id, type(exc).__name__)
            res.failed.append(ae_id)
            try:
                async with sessionmaker() as db:
                    ae = await db.get(AutoExport, ae_id)
                    if ae is not None:
                        ae.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
                        await db.commit()
            except Exception:  # noqa: BLE001
                logger.exception("auto-export: could not record the error for %s", ae_id)
    return res
