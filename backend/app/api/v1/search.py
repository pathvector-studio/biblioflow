"""Hybrid search over indexed paper text (``paper_chunks``): full text + substring + vector, fused with RRF."""

from __future__ import annotations

import re
import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, accessible_workspace_ids, get_paper_for_user, get_workspace_for_user, require_scopes
from app.core.config import get_settings
from app.core.ratelimit import enforce_rate_limit
from app.core.redis import get_redis
from app.core.security import SCOPE_READ_PAPERS
from app.db.session import get_db
from app.models.chunk import PaperChunk
from app.models.paper import Paper
from app.models.workspace import WorkspaceRole
from app.schemas.chunk import SearchHit, SearchStatus
from app.services import embedding
from app.services.search import Mode, rrf, run_search

__all__ = ["router", "rrf"]

router = APIRouter(prefix="/search", tags=["search"])

SEARCH_RATE_LIMIT = 60
SNIPPET_RADIUS = 300
DEGRADED_HEADER = "X-Search-Degraded"
DEGRADED_VALUE = "semantic-unavailable"


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


@router.get("/status", response_model=SearchStatus)
async def search_status(
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> SearchStatus:
    """Is semantic search on, and how far along is the index? The counts cover only the workspaces the caller is
    a member of. Any signed-in user (or read token) may ask; nothing here is about other people's papers."""
    await enforce_rate_limit(get_redis(), "search-status", (str(principal.user.id),), SEARCH_RATE_LIMIT)
    embedder = embedding.get_embedder()
    state = await embedding.model_state(embedder)
    mine = Paper.workspace_id.in_(accessible_workspace_ids(principal.user.id))
    embedded = (
        PaperChunk.embedding.is_not(None) & (PaperChunk.embedding_model == embedder.model_id)
        if embedder is not None
        else false()
    )
    total, done = (
        await db.execute(
            select(func.count(), func.count().filter(embedded))
            .select_from(PaperChunk)
            .join(Paper, Paper.id == PaperChunk.paper_id)
            .where(mine)
        )
    ).one()
    return SearchStatus(
        provider=get_settings().embedding_provider,
        model_state=state,
        chunks_total=total,
        chunks_embedded=done,
        degraded=state != "ready",
    )


@router.get("", response_model=list[SearchHit])
async def search(
    response: Response,
    q: str = Query(min_length=2, max_length=200),
    mode: Mode = "hybrid",
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

    if paper_id is not None:
        paper_id = (await get_paper_for_user(db, paper_id, principal, WorkspaceRole.viewer)).id
    if workspace_id is not None:
        workspace_id = (await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.viewer))[0].id
    outcome = await run_search(
        db, user_id=principal.user.id, q=q, mode=mode, limit=limit, paper_id=paper_id, workspace_id=workspace_id
    )
    if outcome.degraded:
        response.headers[DEGRADED_HEADER] = DEGRADED_VALUE
    return [
        SearchHit(
            paper_id=h.paper.id, workspace_id=h.paper.workspace_id, title=h.paper.title, year=h.paper.year,
            citekey=h.paper.citekey, chunk_id=h.chunk.id, page_number=h.chunk.page_number, section=h.chunk.section,
            snippet=make_snippet(h.chunk.text, q), bboxes=h.chunk.bboxes, score=h.score, matched=h.matched,
        )
        for h in outcome.hits
    ]
