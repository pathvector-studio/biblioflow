"""Keyword search over indexed paper text (``paper_chunks``)."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, accessible_workspace_ids, get_paper_for_user, get_workspace_for_user, require_scopes
from app.core.ratelimit import enforce_rate_limit
from app.core.redis import get_redis
from app.core.security import SCOPE_READ_PAPERS
from app.db.session import get_db
from app.db.sql import LIKE_ESCAPE, escape_like
from app.models.chunk import PaperChunk
from app.models.paper import Paper
from app.models.workspace import WorkspaceRole
from app.schemas.chunk import SearchHit

router = APIRouter(prefix="/search", tags=["search"])

SEARCH_RATE_LIMIT = 60
SNIPPET_RADIUS = 300
SUBSTRING_BONUS = 0.5


def make_snippet(text: str, q: str) -> str:
    """About ``SNIPPET_RADIUS`` characters either side of the first match (whole query, else any word)."""
    m = re.search(re.escape(q), text, re.IGNORECASE)
    if m is None:
        words = [w for w in q.split() if len(w) >= 2]
        if words:
            m = re.search("|".join(re.escape(w) for w in words), text, re.IGNORECASE)
    start, end = (m.start(), m.end()) if m else (0, 0)
    lo, hi = max(0, start - SNIPPET_RADIUS), min(len(text), end + SNIPPET_RADIUS)
    return ("..." if lo > 0 else "") + text[lo:hi].strip() + ("..." if hi < len(text) else "")


@router.get("", response_model=list[SearchHit])
async def search(
    q: str = Query(min_length=2, max_length=200),
    workspace_id: uuid.UUID | None = None,
    paper_id: uuid.UUID | None = None,
    limit: int = Query(20, ge=1, le=50),
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> list[SearchHit]:
    await enforce_rate_limit(get_redis(), "search", (str(principal.user.id),), SEARCH_RATE_LIMIT)
    q = " ".join(q.split())
    if len(q) < 2:
        return []

    tsq = func.websearch_to_tsquery("simple", q)
    substring = PaperChunk.text.ilike(f"%{escape_like(q)}%", escape=LIKE_ESCAPE)
    score = (func.ts_rank_cd(PaperChunk.tsv, tsq) + case((substring, SUBSTRING_BONUS), else_=0.0)).label("score")

    stmt = (
        select(PaperChunk, Paper, score)
        .join(Paper, Paper.id == PaperChunk.paper_id)
        .where(or_(PaperChunk.tsv.op("@@")(tsq), substring))
    )
    if paper_id is not None:
        paper = await get_paper_for_user(db, paper_id, principal, WorkspaceRole.viewer)
        stmt = stmt.where(Paper.id == paper.id)
    if workspace_id is not None:
        ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.viewer)
        stmt = stmt.where(Paper.workspace_id == ws.id)
    stmt = stmt.where(Paper.workspace_id.in_(accessible_workspace_ids(principal.user.id)))
    stmt = stmt.order_by(score.desc(), Paper.id, PaperChunk.chunk_index).limit(limit)

    rows = (await db.execute(stmt)).all()
    return [
        SearchHit(
            paper_id=p.id, workspace_id=p.workspace_id, title=p.title, year=p.year, citekey=p.citekey,
            chunk_id=c.id, page_number=c.page_number, section=c.section, snippet=make_snippet(c.text, q),
            bboxes=c.bboxes, score=round(float(s), 4),
        )
        for c, p, s in rows
    ]
