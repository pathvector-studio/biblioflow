from __future__ import annotations

import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, require_scopes
from app.api.v1.papers import paper_filter
from app.core.ratelimit import enforce_rate_limit
from app.core.redis import get_redis
from app.core.security import SCOPE_READ_PAPERS
from app.db.session import get_db
from app.models.paper import Paper, PaperStatus
from app.services import export as export_svc
from app.services.csl import LOCALES, STYLES, BibliographyError
from app.services.csl.isolated import format_entries_isolated

router = APIRouter(prefix="/bibliography", tags=["bibliography"])

BIBLIOGRAPHY_RATE_LIMIT = 30  # per minute per user (the dialog formats again whenever the style or language changes)
MAX_BIBLIOGRAPHY_PAPERS = 1000


class StyleOut(BaseModel):
    id: str
    title: str
    kind: Literal["author-date", "numeric"]


@router.get("/styles", response_model=list[StyleOut])
async def list_styles(principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS))) -> list[StyleOut]:
    """The bundled citation styles (and, in ``/bibliography``, the locales ``en-US`` and ``ja-JP``)."""
    return [StyleOut(id=s.id, title=s.title, kind=s.kind) for s in STYLES.values()]


def _parse_ids(raw: str | None) -> list[uuid.UUID] | None:
    if raw is None:
        return None
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    if len(parts) > MAX_BIBLIOGRAPHY_PAPERS:
        raise HTTPException(422, f"At most {MAX_BIBLIOGRAPHY_PAPERS} papers can be formatted at once")
    try:
        return [uuid.UUID(p) for p in parts]
    except ValueError:
        raise HTTPException(422, "ids must be a comma-separated list of paper ids") from None


@router.get("")
async def bibliography(
    style: str = Query("apa", max_length=64),
    format: Literal["text", "html"] = "text",
    locale: Literal["en-US", "ja-JP"] = "en-US",
    ids: str | None = Query(None, max_length=1000 * 37),
    q: str | None = Query(None, max_length=200),
    tag: str | None = Query(None, max_length=100),
    collection_id: uuid.UUID | None = None,
    year: int | None = Query(None, ge=1, le=9999),
    status: PaperStatus | None = None,
    workspace_id: uuid.UUID | None = None,
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Formatted references for the papers matching the same filters as ``/papers/light`` (and ``/export``).

    ``ids`` narrows the result to those papers; ids the caller cannot see are left out, as in the list API.
    The papers are listed by title; IEEE, Nature and Vancouver number them in that order, the other styles sort them as the style says.
    The ``html`` body is a fragment in which every field value is escaped (see ``app.services.csl``).
    """
    if style not in STYLES:
        raise HTTPException(422, f"Unknown style: {style}. See /bibliography/styles")
    assert locale in LOCALES
    wanted = _parse_ids(ids)
    await enforce_rate_limit(get_redis(), "bibliography", (str(principal.user.id),), BIBLIOGRAPHY_RATE_LIMIT)
    where = await paper_filter(
        db, principal, q=q, tag=tag, collection_id=collection_id, year=year, status=status, workspace_id=workspace_id
    )
    if wanted is not None:
        where = where & Paper.id.in_(wanted)
    papers = (
        (
            await db.execute(
                select(Paper).where(where).order_by(func.lower(Paper.title), Paper.title, Paper.id).limit(MAX_BIBLIOGRAPHY_PAPERS + 1)
            )
        )
        .scalars()
        .all()
    )
    if len(papers) > MAX_BIBLIOGRAPHY_PAPERS:
        raise HTTPException(422, f"More than {MAX_BIBLIOGRAPHY_PAPERS} papers match; narrow the filters")

    items = [{k: v for k, v in i.items() if k != "abstract"} for i in export_svc.to_csl_items([export_svc.record_from_paper(p) for p in papers])]
    try:
        entries = await format_entries_isolated(items, style, format, locale) if items else []
    except BibliographyError as exc:
        raise HTTPException(422, str(exc)) from None

    headers = {"X-Total-Count": str(len(papers)), "Cache-Control": "no-store"}
    if format == "html":
        body = '<div class="csl-bib-body">\n' + "".join(f'<div class="csl-entry">{e}</div>\n' for e in entries) + "</div>\n"
        # Opened directly in a browser the fragment can run nothing: no scripts, no subresources.
        headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; sandbox"
        media_type = "text/html"
    else:
        body = "".join(f"{e}\n" for e in entries)
        media_type = "text/plain"
    return Response(content=body.encode("utf-8"), media_type=f"{media_type}; charset=utf-8", headers=headers)
