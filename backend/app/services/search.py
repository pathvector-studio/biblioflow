"""Hybrid search core (full text + substring + vector, fused with RRF). Used by ``GET /search`` and the chat.

Callers check access to ``paper_id`` / ``workspace_id`` first (``get_paper_for_user`` etc.); every candidate list is
still filtered by ``accessible_workspace_ids`` here, so a chunk the user may not see never enters the fusion.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import Select, func, select, text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import accessible_workspace_ids
from app.core.config import get_settings
from app.db.sql import LIKE_ESCAPE, escape_like
from app.models.chunk import PaperChunk
from app.models.paper import Paper
from app.services import embedding
from app.services.embedding import EmbeddingError

RRF_K = 60
MIN_CANDIDATES = 50
Mode = Literal["hybrid", "keyword", "semantic"]
logger = logging.getLogger("biblioflow.search")


@dataclass(frozen=True)
class RankedChunk:
    chunk: PaperChunk
    paper: Paper
    score: float
    matched: list[str]


@dataclass(frozen=True)
class SearchOutcome:
    hits: list[RankedChunk]
    degraded: bool  # semantic search was wanted but unavailable; keyword + substring answered


def rrf(rankings: dict[str, list[uuid.UUID]]) -> dict[uuid.UUID, tuple[float, list[str]]]:
    """Reciprocal rank fusion: ``score = sum(1 / (RRF_K + rank))`` over every list that contains the chunk.
    Returns ``{chunk_id: (score, [lists that matched, in the order of ``rankings``])}``."""
    fused: dict[uuid.UUID, tuple[float, list[str]]] = {}
    for name, ids in rankings.items():
        for rank, chunk_id in enumerate(ids, start=1):
            score, names = fused.get(chunk_id, (0.0, []))
            fused[chunk_id] = (score + 1.0 / (RRF_K + rank), [*names, name])
    return fused


async def run_search(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    q: str,
    mode: Mode = "hybrid",
    limit: int = 20,
    paper_id: uuid.UUID | None = None,
    workspace_id: uuid.UUID | None = None,
) -> SearchOutcome:
    """``q`` must already be whitespace-normalised. ``paper_id`` / ``workspace_id`` are access-checked by the caller."""
    allowed = [Paper.workspace_id.in_(accessible_workspace_ids(user_id))]
    if paper_id is not None:
        allowed.append(Paper.id == paper_id)
    if workspace_id is not None:
        allowed.append(Paper.workspace_id == workspace_id)

    def candidates(stmt: Select, order: list, n: int, tiebreak: bool = True) -> Select:
        # The vector list must be ordered by distance alone: a second sort key stops Postgres from using the
        # HNSW index (it falls back to scanning and sorting every chunk). Ties are broken in Python instead.
        order = [*order, PaperChunk.id] if tiebreak else order
        return stmt.join(Paper, Paper.id == PaperChunk.paper_id).where(*allowed).order_by(*order).limit(n)

    n = max(MIN_CANDIDATES, limit * 3)
    semantic: list[uuid.UUID] | None = None
    degraded = False
    lexical = mode != "semantic"
    if mode != "keyword":
        semantic = await _semantic_candidates(db, q, candidates, n)
        if semantic is None:  # keyword + substring still answer
            degraded = True
            lexical = True
    rankings: dict[str, list[uuid.UUID]] = {}
    if lexical:
        rankings.update(await _lexical_candidates(db, q, candidates, n))
    if semantic is not None:
        rankings["semantic"] = semantic

    fused = rrf(rankings)
    top = sorted(fused, key=lambda cid: (-fused[cid][0], str(cid)))[:limit]
    if not top:
        return SearchOutcome([], degraded)
    rows = (
        await db.execute(
            select(PaperChunk, Paper).join(Paper, Paper.id == PaperChunk.paper_id).where(PaperChunk.id.in_(top))
        )
    ).all()
    by_id = {c.id: (c, p) for c, p in rows}
    hits = [
        RankedChunk(by_id[cid][0], by_id[cid][1], round(fused[cid][0], 6), fused[cid][1]) for cid in top if cid in by_id
    ]
    return SearchOutcome(hits, degraded)


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
