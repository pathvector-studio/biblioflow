from __future__ import annotations

import asyncio
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, get_workspace_for_user, require_scopes
from app.core import queue
from app.core.config import get_settings
from app.core.errors import not_found
from app.core.ratelimit import enforce_rate_limit
from app.core.redis import get_redis
from app.core.security import SCOPE_READ_PAPERS, SCOPE_WRITE_PAPERS
from app.db.session import get_db
from app.models.import_job import ImportJob
from app.models.workspace import WorkspaceRole
from app.schemas.import_job import ImportFolderFile, ImportFolderOut, ImportJobCreate, ImportJobOut
from app.services.import_folder import UnsafePath, list_candidates, resolve_in_folder, validate_source_name, workspace_folder
from app.services.import_job import active_job, expire_stale_jobs, job_id_for

logger = logging.getLogger("biblioflow.import_jobs")

router = APIRouter(prefix="/workspaces/{workspace_id}", tags=["import"])

START_RATE_LIMIT = 5  # per minute per user
LIST_LIMIT = 20


def _out(job: ImportJob) -> ImportJobOut:
    return ImportJobOut.model_validate(job, from_attributes=True)


@router.get("/import-folder", response_model=ImportFolderOut)
async def list_import_folder(
    workspace_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> ImportFolderOut:
    """Bibliography files found in ``data/import/<workspace-slug>/`` on the server (editors and owners)."""
    ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.editor)
    folder = workspace_folder(get_settings().import_dir, ws.slug)
    files = await asyncio.to_thread(list_candidates, folder)
    return ImportFolderOut(
        slug=ws.slug, exists=await asyncio.to_thread(folder.is_dir),
        files=[ImportFolderFile(name=c.name, size=c.size, format=c.format) for c in files],  # type: ignore[arg-type]
    )


@router.post("/import-jobs", response_model=ImportJobOut, status_code=202)
async def start_import_job(
    workspace_id: uuid.UUID,
    body: ImportJobCreate,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> ImportJobOut:
    """Import a bibliography file from the workspace's server folder, with the PDFs it points to.

    The file is named, never uploaded: ``source`` must be a path inside the folder (see ``import-folder``).
    One job per workspace at a time (409)."""
    await enforce_rate_limit(get_redis(), "import_job", (str(principal.user.id),), START_RATE_LIMIT)
    ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.editor)
    folder = workspace_folder(get_settings().import_dir, ws.slug)
    try:
        source = validate_source_name(body.source)
        await asyncio.to_thread(resolve_in_folder, folder, source)
    except UnsafePath as exc:
        raise HTTPException(status_code=422, detail=f"Cannot use this file: {exc}") from None

    await expire_stale_jobs(db, ws.id)
    await db.commit()
    if await active_job(db, ws.id) is not None:
        raise HTTPException(status_code=409, detail="An import is already running in this workspace")
    job = ImportJob(workspace_id=ws.id, user_id=principal.user.id, source=source, status="queued")
    db.add(job)
    try:
        await db.commit()  # the partial unique index decides a race between two clicks
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="An import is already running in this workspace") from None
    await db.refresh(job)
    try:
        pool = await queue.get_arq_pool()
        await pool.enqueue_job("run_import_job", str(job.id), _job_id=job_id_for(job.id))
    except Exception:  # noqa: BLE001
        logger.warning("failed to enqueue import job %s", job.id, exc_info=True)
        job.status, job.error = "failed", "The import could not be queued."
        await db.commit()
        raise HTTPException(status_code=503, detail="The import could not be queued") from None
    return _out(job)


@router.get("/import-jobs", response_model=list[ImportJobOut])
async def list_import_jobs(
    workspace_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> list[ImportJobOut]:
    ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.viewer)
    rows = (
        await db.execute(
            select(ImportJob).where(ImportJob.workspace_id == ws.id).order_by(ImportJob.created_at.desc()).limit(LIST_LIMIT)
        )
    ).scalars()
    return [_out(j) for j in rows]


@router.get("/import-jobs/{job_id}", response_model=ImportJobOut)
async def get_import_job(
    workspace_id: uuid.UUID,
    job_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> ImportJobOut:
    ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.viewer)
    job = (
        await db.execute(select(ImportJob).where(ImportJob.id == job_id, ImportJob.workspace_id == ws.id))
    ).scalar_one_or_none()
    if job is None:
        raise not_found()
    return _out(job)
