"""Hybrid search over indexed paper text (``paper_chunks``): full text + substring + vector, fused with RRF."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import Select, false, func, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, accessible_workspace_ids, get_paper_for_user, get_workspace_for_user, require_scopes
from app.core.config import get_settings
from app.core.ratelimit import enforce_rate_limit
from app.core.redis import get_redis
from app.core.security import SCOPE_READ_PAPERS
from app.db.session import get_db
from app.db.sql import LIKE_ESCAPE, escape_like
from app.models.chunk import PaperChunk
from app.models.paper import Paper
from app.models.workspace import WorkspaceRole
from app.schemas.chunk import SearchHit, SearchStatus
from app.services import embedding
from app.services.embedding import EmbeddingError

router = APIRouter(prefix="/search", tags=["search"])

SEARCH_RATE_LIMIT = 60
SNIPPET_RADIUS = 300
RRF_K = 60
MIN_CANDIDATES = 50
DEGRADED_HEADER = "X-Search-Degraded"
DEGRADED_VALUE = "semantic-unavailable"
Mode = Literal["hybrid", "keyword", "semantic"]
logger = logging.getLogger("biblioflow.search")


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


def rrf(rankings: dict[str, list[uuid.UUID]]) -> dict[uuid.UUID, tuple[float, list[str]]]:
    """Reciprocal rank fusion: ``score = sum(1 / (RRF_K + rank))`` over every list that contains the chunk.
    Returns ``{chunk_id: (score, [lists that matched, in the order of ``rankings``])}``."""
    fused: dict[uuid.UUID, tuple[float, list[str]]] = {}
    for name, ids in rankings.items():
        for rank, chunk_id in enumerate(ids, start=1):
            score, names = fused.get(chunk_id, (0.0, []))
            fused[chunk_id] = (score + 1.0 / (RRF_K + rank), [*names, name])
    return fused


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

    # Every candidate list is filtered by the same permission predicate, so a chunk the caller may not see can
    # never enter the fusion through any of them.
    allowed = [Paper.workspace_id.in_(accessible_workspace_ids(principal.user.id))]
    if paper_id is not None:
        paper = await get_paper_for_user(db, paper_id, principal, WorkspaceRole.viewer)
        allowed.append(Paper.id == paper.id)
    if workspace_id is not None:
        ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.viewer)
        allowed.append(Paper.workspace_id == ws.id)

    def candidates(stmt: Select, order: list, n: int, tiebreak: bool = True) -> Select:
        # The vector list must be ordered by distance alone: a second sort key stops Postgres from using the
        # HNSW index (it falls back to scanning and sorting every chunk). Ties are broken in Python instead.
        order = [*order, PaperChunk.id] if tiebreak else order
        return stmt.join(Paper, Paper.id == PaperChunk.paper_id).where(*allowed).order_by(*order).limit(n)

    n = max(MIN_CANDIDATES, limit * 3)
    semantic: list[uuid.UUID] | None = None
    lexical = mode != "semantic"
    if mode != "keyword":
        semantic = await _semantic_candidates(db, q, candidates, n)
        if semantic is None:  # keyword + substring still answer
            response.headers[DEGRADED_HEADER] = DEGRADED_VALUE
            lexical = True
    rankings: dict[str, list[uuid.UUID]] = {}
    if lexical:
        rankings.update(await _lexical_candidates(db, q, candidates, n))
    if semantic is not None:
        rankings["semantic"] = semantic

    fused = rrf(rankings)
    top = sorted(fused, key=lambda cid: (-fused[cid][0], str(cid)))[:limit]
    if not top:
        return []
    rows = (
        await db.execute(
            select(PaperChunk, Paper).join(Paper, Paper.id == PaperChunk.paper_id).where(PaperChunk.id.in_(top))
        )
    ).all()
    by_id = {c.id: (c, p) for c, p in rows}
    return [
        SearchHit(
            paper_id=p.id, workspace_id=p.workspace_id, title=p.title, year=p.year, citekey=p.citekey,
            chunk_id=c.id, page_number=c.page_number, section=c.section, snippet=make_snippet(c.text, q),
            bboxes=c.bboxes, score=round(fused[cid][0], 6), matched=fused[cid][1],
        )
        for cid in top
        if (cp := by_id.get(cid)) is not None
        for c, p in [cp]
    ]


async def _semantic_candidates(db: AsyncSession, q: str, candidates, n: int) -> list[uuid.UUID] | None:
    """Chunk ids closest to the query, best first; ``None`` when semantic search is unavailable (no provider,
    embedder down or slower than ``EMBEDDING_QUERY_TIMEOUT``)."""
    embedder = embedding.get_embedder()
    if embedder is None:
        return None
    settings = get_settings()
    try:
        (qvec,) = await embedder.embed([q], "query", timeout=settings.embedding_query_timeout)
    except EmbeddingError as exc:
        logger.warning("semantic search unavailable: %s", exc)
        return None
    # Iterative scan keeps the HNSW index returning rows after the permission filter has dropped some.
    await db.execute(sql_text("SET LOCAL hnsw.iterative_scan = relaxed_order"))
    await db.execute(sql_text("SET LOCAL hnsw.ef_search = 100"))
    distance = PaperChunk.embedding.cosine_distance(qvec).label("distance")
    stmt = candidates(
        select(PaperChunk.id, distance).where(
            PaperChunk.embedding.is_not(None), PaperChunk.embedding_model == embedder.model_id
        ),
        [distance],
        n,
        tiebreak=False,
    )
    rows = (await db.execute(stmt)).all()
    floor = settings.search_min_similarity
    # relaxed_order may hand rows back slightly out of order; sort again here.
    rows = sorted((r for r in rows if 1.0 - float(r.distance) >= floor), key=lambda r: (r.distance, str(r.id)))
    return [r.id for r in rows]


async def _lexical_candidates(db: AsyncSession, q: str, candidates, n: int) -> dict[str, list[uuid.UUID]]:
    tsq = func.websearch_to_tsquery("simple", q)
    substring = PaperChunk.text.ilike(f"%{escape_like(q)}%", escape=LIKE_ESCAPE)
    keyword = candidates(
        select(PaperChunk.id).where(PaperChunk.tsv.op("@@")(tsq)), [func.ts_rank_cd(PaperChunk.tsv, tsq).desc()], n
    )
    # Scripts without word boundaries (Japanese) match here.
    partial = candidates(
        select(PaperChunk.id).where(substring), [func.word_similarity(q, PaperChunk.text).desc()], n
    )
    return {
        "keyword": list((await db.execute(keyword)).scalars()),
        "substring": list((await db.execute(partial)).scalars()),
    }
