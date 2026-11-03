"""arXiv Atom API client (XML parsed with defusedxml)."""

from __future__ import annotations

import re
from xml.etree.ElementTree import Element

import httpx
from defusedxml import ElementTree as SafeET
from defusedxml.common import DefusedXmlException

from app.models.paper import IdentifierType
from app.schemas.metadata import PaperMetadata, UpstreamError
from app.services.metadata._http import fetch_bytes
from app.services.metadata.names import clean, split_name

ARXIV_URL = "https://export.arxiv.org/api/query"
_NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom"}


def _text(entry: Element, path: str) -> str:
    el = entry.find(path, _NS)
    return clean(el.text) if el is not None else ""


class ArxivClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def get(self, arxiv_id: str) -> PaperMetadata | None:
        body = await fetch_bytes(self._client, ARXIV_URL, params={"id_list": arxiv_id, "max_results": 1})
        try:
            root = SafeET.fromstring(body)
        except (SafeET.ParseError, DefusedXmlException, ValueError) as exc:
            raise UpstreamError("arXiv returned an unparseable response") from exc
        entry = root.find("a:entry", _NS)
        if entry is None:
            return None
        entry_id = _text(entry, "a:id")
        title = _text(entry, "a:title")
        if "api/errors" in entry_id or title.lower() == "error" or not title:
            return None
        m = re.search(r"/abs/(.+?)(?:v\d+)?$", entry_id)
        aid = m.group(1) if m else arxiv_id
        authors = [
            a
            for el in entry.findall("a:author/a:name", _NS)
            if (a := split_name(el.text or "")) is not None
        ]
        year = None
        ym = re.match(r"(\d{4})", _text(entry, "a:published"))
        if ym:
            year = int(ym.group(1))
        doi = _text(entry, "x:doi").lower() or None
        return PaperMetadata(
            identifier=aid,
            identifier_type=IdentifierType.arxiv,
            doi=doi,
            arxiv_id=aid,
            title=title,
            authors=authors,
            year=year,
            venue=_text(entry, "x:journal_ref") or "arXiv",
            abstract=_text(entry, "a:summary") or None,
            source="arxiv",
        )
