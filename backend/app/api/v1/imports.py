from __future__ import annotations

import asyncio
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, require_scopes
from app.api.v1.papers import _workspace
from app.core.ratelimit import enforce_rate_limit
from app.core.redis import get_redis
from app.core.security import SCOPE_WRITE_PAPERS
from app.db.session import get_db
from app.models.workspace import WorkspaceRole
from app.schemas.imports import ImportItemOut, ImportSummary
from app.services import import_apply
from app.services.importers import ImportFormatError, parse_file

router = APIRouter(prefix="/imports", tags=["imports"])

MAX_IMPORT_FILE = 4 * 1024 * 1024
# The multipart envelope adds a little to the file: these two routes accept 5 MiB of body (app/main.py and
# deploy/Caddyfile carry the same number).
IMPORT_BODY_LIMIT = 5 * 1024 * 1024
MAX_ITEMS = 10_000
MAX_LISTED = 100
SAMPLE_SIZE = 20
PREVIEW_RATE_LIMIT = 20  # per minute per user
COMMIT_RATE_LIMIT = 5


async def _read_file(file: UploadFile) -> bytes:
    data = await file.read(MAX_IMPORT_FILE + 1)
    if len(data) > MAX_IMPORT_FILE:
        raise HTTPException(status_code=413, detail="File too large (4 MiB at most)")
    if not data.strip():
        raise HTTPException(status_code=422, detail="The file is empty")
    return data


def _out(r: import_apply.ItemResult) -> ImportItemOut:
    return ImportItemOut(
        ref=r.ref[:200], title=r.title[:300], status=r.status, identifier=r.identifier, matched_by=r.matched_by,
        reason=r.reason,
    )


async def _run(
    file: UploadFile, workspace_id: uuid.UUID | None, principal: Principal, db: AsyncSession, *, dry_run: bool
) -> ImportSummary:
    await enforce_rate_limit(
        get_redis(), "import_preview" if dry_run else "import_commit", (str(principal.user.id),),
        PREVIEW_RATE_LIMIT if dry_run else COMMIT_RATE_LIMIT,
    )
    ws = await _workspace(db, principal, workspace_id, WorkspaceRole.editor)
    data = await _read_file(file)
    try:
        parsed = await asyncio.to_thread(parse_file, data, file.filename)
    except ImportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    if len(parsed.items) + len(parsed.problems) > MAX_ITEMS:
        raise HTTPException(status_code=422, detail=f"Too many records (at most {MAX_ITEMS} per file)")

    outcome = await import_apply.run_import(db, workspace=ws, user_id=principal.user.id, parsed=parsed, dry_run=dry_run)
    if dry_run:
        await db.rollback()
    else:
        await db.commit()

    bad = [r for r in outcome.results if r.status == "unreadable"]
    return ImportSummary(
        format=parsed.format,  # type: ignore[arg-type]
        dry_run=dry_run,
        total=len(outcome.results),
        new=outcome.count("new"),
        matched=outcome.count("matched"),
        unreadable=len(bad),
        with_attachments=outcome.with_attachments,
        tags_added=outcome.tags_added,
        collections_created=outcome.collections_created,
        collection_links=outcome.collection_links,
        notes_added=outcome.notes_added,
        problems=[_out(r) for r in bad[:MAX_LISTED]],
        items=[_out(r) for r in outcome.results if r.status != "unreadable"][:SAMPLE_SIZE],
        problems_truncated=len(bad) > MAX_LISTED,
    )


@router.post("/preview", response_model=ImportSummary)
async def preview_import(
    file: UploadFile = File(..., description="BibTeX, RIS, CSL JSON or Zotero RDF (4 MiB at most)"),
    workspace_id: uuid.UUID | None = Form(None),
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> ImportSummary:
    """Read the file and say how many records would be new, matched to existing papers, or unreadable. Writes nothing."""
    return await _run(file, workspace_id, principal, db, dry_run=True)


@router.post("", response_model=ImportSummary)
async def commit_import(
    file: UploadFile = File(..., description="BibTeX, RIS, CSL JSON or Zotero RDF (4 MiB at most)"),
    workspace_id: uuid.UUID | None = Form(None),
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> ImportSummary:
    """Import the bibliographic records as wishlist papers (metadata only; no external lookups, no PDFs).

    One transaction per request; a record that cannot be saved is reported and skipped, the rest are imported.
    """
    return await _run(file, workspace_id, principal, db, dry_run=False)
