"""Semantic Scholar Graph API client (title match)."""

from __future__ import annotations

import httpx

from app.models.paper import IdentifierType
from app.schemas.metadata import PaperMetadata, UpstreamError
from app.services.metadata._http import fetch_json
from app.services.metadata.names import clean, split_name

MATCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search/match"
FIELDS = "title,authors,year,venue,abstract,externalIds"


class SemanticScholarClient:
    def __init__(self, client: httpx.AsyncClient, api_key: str | None = None) -> None:
        self._client = client
        self._headers = {"x-api-key": api_key} if api_key else None

    async def match(self, title: str) -> PaperMetadata | None:
        data = await fetch_json(
            self._client,
            MATCH_URL,
            params={"query": title, "fields": FIELDS},
            headers=self._headers,
            not_found_ok=True,
        )
        if data is None:
            return None
        if not isinstance(data, dict) or not isinstance(data.get("data", []), list):
            raise UpstreamError("Semantic Scholar returned an unexpected payload")
        rows = data.get("data") or []
        if not rows or not isinstance(rows[0], dict):
            return None
        item = rows[0]
        ext = item.get("externalIds") or {}
        arxiv_id = clean(ext.get("ArXiv")) or None
        doi = clean(ext.get("DOI")).lower() or None
        t = clean(item.get("title"))
        if not t or not (arxiv_id or doi):
            return None
        year = item.get("year")
        return PaperMetadata(
            identifier=arxiv_id or doi,  # type: ignore[arg-type]
            identifier_type=IdentifierType.arxiv if arxiv_id else IdentifierType.doi,
            doi=doi,
            arxiv_id=arxiv_id,
            title=t,
            authors=[
                a
                for au in item.get("authors") or []
                if isinstance(au, dict) and (a := split_name(au.get("name") or "")) is not None
            ],
            year=year if isinstance(year, int) else None,
            venue=clean(item.get("venue")) or None,
            abstract=clean(item.get("abstract")) or None,
            source="semantic_scholar",
        )
