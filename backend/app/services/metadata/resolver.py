"""Query -> external APIs -> merged, cached ``LookupResult``."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.net import safe_client
from app.core.redis import get_redis
from app.schemas.metadata import LookupItem, LookupKind, LookupResult, PaperMetadata, UpstreamError
from app.services.metadata.arxiv import ArxivClient
from app.services.metadata.bibtex import citekey_base, make_bibtex
from app.services.metadata.crossref import CrossrefClient
from app.services.metadata.identifiers import parse_query
from app.services.metadata.semantic_scholar import SemanticScholarClient

log = logging.getLogger(__name__)

ALLOWED_HOSTS = {"export.arxiv.org", "api.crossref.org", "api.semanticscholar.org"}
MAX_QUERY_LEN = 500
CACHE_TTL = 24 * 3600
EMPTY_CACHE_TTL = 10 * 60  # misses are retried sooner: the record may be indexed shortly


class DefaultResolver:
    def __init__(
        self,
        arxiv: ArxivClient,
        crossref: CrossrefClient,
        s2: SemanticScholarClient,
        redis: Any | None = None,
    ) -> None:
        self._arxiv = arxiv
        self._crossref = crossref
        self._s2 = s2
        self._redis = redis

    async def _cache_get(self, key: str) -> LookupResult | None:
        if self._redis is None:
            return None
        try:
            raw = await self._redis.get(key)
            return LookupResult.model_validate_json(raw) if raw else None
        except Exception:  # noqa: BLE001 - cache must never break resolution
            log.warning("lookup cache read failed", exc_info=True)
            return None

    async def _cache_set(self, key: str, result: LookupResult) -> None:
        if self._redis is None:
            return
        try:
            await self._redis.set(key, result.model_dump_json(), ex=CACHE_TTL if result.results else EMPTY_CACHE_TTL)
        except Exception:  # noqa: BLE001
            log.warning("lookup cache write failed", exc_info=True)

    async def resolve(self, query: str, *, limit: int = 5) -> LookupResult:
        q = query.strip()
        if not q:
            raise ValueError("query is empty")
        if len(q) > MAX_QUERY_LEN:
            raise ValueError(f"query exceeds {MAX_QUERY_LEN} characters")
        limit = max(1, min(limit, 20))
        parsed = parse_query(q)
        kind: LookupKind = "doi" if parsed.kind == "pii" else parsed.kind
        digest = hashlib.sha256(f"{parsed.kind}:{parsed.value}:{limit}".encode()).hexdigest()[:32]
        key = f"bf:lookup:{digest}"
        cached = await self._cache_get(key)
        if cached is not None:
            return cached.model_copy(update={"query": q})

        cacheable = True
        found: list[PaperMetadata] = []
        if parsed.kind == "arxiv":
            meta = await self._arxiv.get(parsed.value)
            found = [meta] if meta else []
        elif parsed.kind == "doi":
            meta = await self._crossref.get(parsed.value)
            found = [meta] if meta else []
        elif parsed.kind == "pii":
            meta = await self._crossref.get_by_pii(parsed.value)
            found = [meta] if meta else []
        else:
            cr, s2 = await asyncio.gather(
                self._crossref.search(parsed.value, limit),
                self._s2.match(parsed.value),
                return_exceptions=True,
            )
            if isinstance(cr, BaseException):
                if not isinstance(cr, UpstreamError):
                    raise UpstreamError("metadata lookup failed") from cr
                raise cr
            if isinstance(s2, BaseException):
                # Semantic Scholar rate-limits aggressively; CrossRef results still serve.
                log.warning("semantic scholar match failed: %s", type(s2).__name__)
                cacheable = False
                s2 = None
            found = _merge(([s2] if s2 else []) + cr)[:limit]

        result = LookupResult(
            query=q,
            kind=kind,
            results=[
                LookupItem(**m.model_dump(), bibtex=make_bibtex(m, citekey_base(m))) for m in found
            ],
        )
        if cacheable:
            await self._cache_set(key, result)
        return result


def _merge(items: list[PaperMetadata]) -> list[PaperMetadata]:
    seen: set[str] = set()
    out: list[PaperMetadata] = []
    for m in items:
        keys = {k for k in (m.doi and f"doi:{m.doi}", m.arxiv_id and f"arxiv:{m.arxiv_id}") if k}
        if keys & seen:
            continue
        seen |= keys
        out.append(m)
    return out


_client: httpx.AsyncClient | None = None


def build_default_resolver() -> DefaultResolver:
    """Build a resolver sharing one lazily created module-level HTTP client (connection reuse)."""
    global _client
    settings = get_settings()
    if _client is None or _client.is_closed:
        _client = safe_client(allowed_hosts=ALLOWED_HOSTS)
    key = settings.semantic_scholar_api_key
    return DefaultResolver(
        ArxivClient(_client),
        CrossrefClient(_client, settings.crossref_mailto),
        SemanticScholarClient(_client, key.get_secret_value() if key else None),
        get_redis(),
    )
