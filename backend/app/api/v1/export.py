from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, require_scopes
from app.api.v1.papers import paper_filter
from app.core.ratelimit import enforce_rate_limit
from app.core.redis import get_redis
from app.core.security import SCOPE_READ_PAPERS
from app.db.session import get_db
from app.models.paper import Paper, PaperStatus
from app.services import export as export_svc

router = APIRouter(prefix="/export", tags=["export"])

EXPORT_RATE_LIMIT = 10  # per minute per user
MAX_EXPORT_PAPERS = 20000


@router.get("")
async def export_papers(
    format: Literal["bibtex", "ris", "csljson"] = "bibtex",
    q: str | None = Query(None, max_length=200),
    tag: str | None = Query(None, max_length=100),
    collection_id: uuid.UUID | None = None,
    year: int | None = Query(None, ge=1, le=9999),
    status: PaperStatus | None = None,
    workspace_id: uuid.UUID | None = None,
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Download the papers matching the same filters as ``/papers/light`` as BibTeX, RIS or CSL JSON."""
    await enforce_rate_limit(get_redis(), "export", (str(principal.user.id),), EXPORT_RATE_LIMIT)
    where = await paper_filter(
        db, principal, q=q, tag=tag, collection_id=collection_id, year=year, status=status, workspace_id=workspace_id
    )
    papers = (
        (await db.execute(select(Paper).where(where).order_by(Paper.created_at, Paper.id).limit(MAX_EXPORT_PAPERS)))
        .scalars()
        .all()
    )
    body = export_svc.render(format, [export_svc.record_from_paper(p) for p in papers])
    media_type, ext = export_svc.FORMATS[format]
    return Response(
        content=body.encode("utf-8"),
        media_type=f"{media_type}; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="biblioflow-export.{ext}"',
            "X-Total-Count": str(len(papers)),
            "Cache-Control": "no-store",
        },
    )
