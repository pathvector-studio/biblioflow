"""Contract between metadata resolution (services/metadata) and ingest/API layers."""

from __future__ import annotations

from typing import Literal, Protocol

from pydantic import BaseModel, Field

from app.models.paper import IdentifierType

MetadataSource = Literal["arxiv", "crossref", "semantic_scholar", "pdf", "manual"]
LookupKind = Literal["arxiv", "doi", "title"]


class Author(BaseModel):
    family: str = Field(min_length=1, max_length=200)
    given: str | None = Field(default=None, max_length=200)


class PaperMetadata(BaseModel):
    """Normalized bibliographic record. ``identifier`` precedence: arxiv > doi > internal."""

    identifier: str
    identifier_type: IdentifierType
    doi: str | None = None  # lowercase, without "https://doi.org/"
    arxiv_id: str | None = None  # without version suffix, e.g. "2405.12345" / "hep-th/9901001"
    title: str
    authors: list[Author] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    source: MetadataSource


class LookupItem(PaperMetadata):
    bibtex: str


class LookupResult(BaseModel):
    query: str
    kind: LookupKind
    results: list[LookupItem] = Field(default_factory=list)


class UpstreamError(Exception):
    """An external metadata API failed (network, HTTP error, malformed payload, SSRF block)."""


class MetadataResolver(Protocol):
    async def resolve(self, query: str, *, limit: int = 5) -> LookupResult:
        """Resolve an arXiv id / DOI / URL / free-text title. Raises ``UpstreamError``."""
        ...
