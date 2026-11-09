from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, get_workspace_for_user, require_scopes
from app.core.config import get_settings
from app.core.errors import not_found
from app.core.security import SCOPE_READ_PAPERS, SCOPE_WRITE_PAPERS
from app.db.session import get_db
from app.models.auto_export import AutoExport
from app.models.collection import Collection
from app.models.workspace import Workspace, WorkspaceRole
from app.schemas.auto_export import AutoExportCreate, AutoExportOut
from app.services.auto_export import MAX_PER_WORKSPACE, delete_export_file

router = APIRouter(prefix="/workspaces/{workspace_id}/auto-exports", tags=["export"])


def _out(ae: AutoExport, ws: Workspace) -> AutoExportOut:
    return AutoExportOut(
        id=ae.id, workspace_id=ae.workspace_id, filename=ae.filename, format=ae.format,
        path=f"{ws.slug}/{ae.filename}", q=ae.q, tag=ae.tag, collection_id=ae.collection_id, year=ae.year,
        status=ae.status, paper_count=ae.paper_count, last_written_at=ae.last_written_at,
        last_error=ae.last_error, created_at=ae.created_at,
    )


@router.get("", response_model=list[AutoExportOut])
async def list_auto_exports(
    workspace_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> list[AutoExportOut]:
    ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.viewer)
    rows = (
        (await db.execute(select(AutoExport).where(AutoExport.workspace_id == ws.id).order_by(AutoExport.created_at)))
        .scalars()
        .all()
    )
    return [_out(a, ws) for a in rows]


@router.post("", response_model=AutoExportOut, status_code=201)
async def create_auto_export(
    workspace_id: uuid.UUID,
    body: AutoExportCreate,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> AutoExportOut:
    """Register a standing export. The worker writes the file within a minute and rewrites it after every change."""
    ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.editor)
    if body.collection_id is not None:
        owned = (
            await db.execute(
                select(Collection.id).where(Collection.id == body.collection_id, Collection.workspace_id == ws.id)
            )
        ).first()
        if owned is None:
            raise not_found()
    n = (await db.execute(select(func.count()).select_from(AutoExport).where(AutoExport.workspace_id == ws.id))).scalar_one()
    if n >= MAX_PER_WORKSPACE:
        raise HTTPException(status_code=409, detail=f"At most {MAX_PER_WORKSPACE} auto-exports per workspace")
    ae = AutoExport(
        workspace_id=ws.id, filename=body.filename, format=body.format, q=(body.q or "").strip() or None,
        tag=(body.tag or "").strip() or None, collection_id=body.collection_id, year=body.year,
        status=body.status.value if body.status else None, created_by=principal.user.id,
    )
    db.add(ae)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="An auto-export with this filename already exists") from None
    await db.refresh(ae)
    return _out(ae, ws)


@router.delete("/{auto_export_id}", status_code=204)
async def delete_auto_export(
    workspace_id: uuid.UUID,
    auto_export_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.editor)
    ae = (
        await db.execute(select(AutoExport).where(AutoExport.id == auto_export_id, AutoExport.workspace_id == ws.id))
    ).scalar_one_or_none()
    if ae is None:
        raise not_found()
    filename = ae.filename
    await db.delete(ae)
    await db.commit()
    delete_export_file(get_settings().export_dir, ws.slug, filename)
    return Response(status_code=204)
