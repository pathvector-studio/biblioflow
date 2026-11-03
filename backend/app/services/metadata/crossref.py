"""CrossRef REST client."""

from __future__ import annotations

import html
import re
from typing import Any
from urllib.parse import quote

import httpx

from app.models.paper import IdentifierType
from app.schemas.metadata import Author, PaperMetadata, UpstreamError
from app.services.metadata._http import fetch_json
from app.services.metadata.names import clean

BASE = "https://api.crossref.org"
_SELECT = "DOI,title,author,issued,published,published-print,published-online,container-title,abstract,type"
_ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+?)(?:v\d+)?$", re.IGNORECASE)


def _text(value: Any) -> str:
    """CrossRef strings are HTML-escaped and may carry inline markup (<i>, <sub>, JATS)."""
    if not isinstance(value, str):
        return ""
    return clean(html.unescape(re.sub(r"<[^>]+>", "", value)))


def _first(value: Any) -> str:
    if isinstance(value, list):
        value = value[0] if value else ""
    return _text(value)


def _year(item: dict[str, Any]) -> int | None:
    for key in ("issued", "published-print", "published-online", "published"):
        try:
            y = item[key]["date-parts"][0][0]
        except (KeyError, IndexError, TypeError):
            continue
        if isinstance(y, int) and 1000 <= y <= 3000:
            return y
    return None


def _authors(item: dict[str, Any]) -> list[Author]:
    out: list[Author] = []
    for a in item.get("author") or []:
        if not isinstance(a, dict):
            continue
        family = _text(a.get("family")) or _text(a.get("name"))
        if family:
            out.append(Author(family=family[:200], given=_text(a.get("given"))[:200] or None))
    return out


def to_metadata(item: Any) -> PaperMetadata | None:
    if not isinstance(item, dict):
        return None
    doi = clean(item.get("DOI")).lower()
    title = _first(item.get("title"))
    if not doi or not title:
        return None
    abstract = item.get("abstract")
    if isinstance(abstract, str):
        abstract = clean(html.unescape(re.sub(r"<[^>]+>", " ", abstract))) or None
    else:
        abstract = None
    m = _ARXIV_DOI.match(doi)
    if m:
        ident, itype, arxiv_id = m.group(1), IdentifierType.arxiv, m.group(1)
    else:
        ident, itype, arxiv_id = doi, IdentifierType.doi, None
    return PaperMetadata(
        identifier=ident,
        identifier_type=itype,
        doi=doi,
        arxiv_id=arxiv_id,
        title=title,
        authors=_authors(item),
        year=_year(item),
        venue=_first(item.get("container-title")) or None,
        abstract=abstract,
        source="crossref",
    )


class CrossrefClient:
    def __init__(self, client: httpx.AsyncClient, mailto: str | None = None) -> None:
        self._client = client
        self._mailto = mailto

    def _params(self, **params: Any) -> dict[str, Any]:
        if self._mailto:
            params["mailto"] = self._mailto
        return params

    @staticmethod
    def _message(data: Any) -> dict[str, Any]:
        msg = data.get("message") if isinstance(data, dict) else None
        if not isinstance(msg, dict):
            raise UpstreamError("CrossRef returned an unexpected payload")
        return msg

    async def get(self, doi: str) -> PaperMetadata | None:
        data = await fetch_json(
            self._client,
            f"{BASE}/works/{quote(doi, safe='/')}",
            params=self._params(),
            not_found_ok=True,
        )
        return None if data is None else to_metadata(self._message(data))

    async def get_by_pii(self, pii: str) -> PaperMetadata | None:
        data = await fetch_json(
            self._client,
            f"{BASE}/works",
            params=self._params(filter=f"alternative-id:{pii}", rows=1),
        )
        items = self._message(data).get("items") or []
        return to_metadata(items[0]) if items else None

    async def search(self, title: str, limit: int = 5) -> list[PaperMetadata]:
        data = await fetch_json(
            self._client,
            f"{BASE}/works",
            params=self._params(**{"query.bibliographic": title, "rows": limit, "select": _SELECT}),
        )
        items = self._message(data).get("items") or []
        return [m for it in items if (m := to_metadata(it)) is not None]
