from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from app.models.paper import IdentifierType
from app.schemas.metadata import UpstreamError
from app.services.metadata.arxiv import ArxivClient
from app.services.metadata.crossref import CrossrefClient
from app.services.metadata.resolver import DefaultResolver
from app.services.metadata.semantic_scholar import SemanticScholarClient

FIX = Path(__file__).parent / "fixtures"


def fx(name: str) -> bytes:
    return (FIX / name).read_bytes()


def client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, k):
        return self.store.get(k)

    async def set(self, k, v, ex=None):
        self.store[k] = v


class BrokenRedis:
    async def get(self, k):
        raise ConnectionError

    async def set(self, k, v, ex=None):
        raise ConnectionError


def router(calls: list[httpx.Request] | None = None):
    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(req)
        host, path = req.url.host, req.url.path
        if host == "export.arxiv.org":
            if req.url.params["id_list"] == "1234.5678":
                return httpx.Response(200, content=fx("arxiv_error.xml"))
            return httpx.Response(200, content=fx("arxiv_2405.xml"))
        if host == "api.crossref.org":
            if path == "/works/10.1145/3651890":
                return httpx.Response(200, content=fx("crossref_work.json"))
            if path.startswith("/works/"):
                return httpx.Response(404)
            if "filter" in req.url.params:
                return httpx.Response(200, content=fx("crossref_pii.json"))
            return httpx.Response(200, content=fx("crossref_search.json"))
        if host == "api.semanticscholar.org":
            return httpx.Response(200, content=fx("s2_match.json"))
        return httpx.Response(404)

    return handler


def resolver(c, redis=None) -> DefaultResolver:
    return DefaultResolver(ArxivClient(c), CrossrefClient(c, "me@example.com"), SemanticScholarClient(c, "k"), redis)


async def test_arxiv_get() -> None:
    m = await ArxivClient(client(router())).get("2405.12345")
    assert m is not None
    assert (m.identifier, m.identifier_type, m.arxiv_id, m.source) == ("2405.12345", IdentifierType.arxiv, "2405.12345", "arxiv")
    assert m.title == "Scalable BGP Routing in Clos Fabrics"
    assert [(a.family, a.given) for a in m.authors] == [("Lovelace", "Ada B."), ("Turing", "Alan")]
    assert m.year == 2024 and m.doi == "10.1145/3651890" and m.venue == "SIGCOMM 2024"
    assert m.abstract == "We study BGP routing in large Clos fabrics."


async def test_arxiv_error_entry_and_failures() -> None:
    assert await ArxivClient(client(router())).get("1234.5678") is None
    with pytest.raises(UpstreamError):
        await ArxivClient(client(lambda r: httpx.Response(500))).get("2405.12345")
    with pytest.raises(UpstreamError):
        await ArxivClient(client(lambda r: httpx.Response(200, content=b"<feed><oops"))).get("2405.12345")


@pytest.mark.parametrize(
    "payload",
    [
        b'<?xml version="1.0"?><!DOCTYPE f [<!ENTITY x SYSTEM "file:///etc/passwd">]><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>&x;</title></entry></feed>',
        b'<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><feed>&b;</feed>',
    ],
)
async def test_arxiv_rejects_xxe_and_entity_bombs(payload: bytes) -> None:
    with pytest.raises(UpstreamError):
        await ArxivClient(client(lambda r: httpx.Response(200, content=payload))).get("2405.12345")


async def test_crossref_get_search_pii() -> None:
    calls: list[httpx.Request] = []
    cr = CrossrefClient(client(router(calls)), "me@example.com")
    m = await cr.get("10.1145/3651890")
    assert m is not None and m.identifier_type == IdentifierType.doi and m.year == 2024
    assert m.venue == "Proceedings of the ACM on Networking"
    assert m.abstract == "We study BGP routing."
    assert [(a.family, a.given) for a in m.authors] == [("Lovelace", "Ada"), ("ACME Consortium", None)]
    assert calls[0].url.params["mailto"] == "me@example.com"
    assert await cr.get("10.9999/missing") is None

    res = await cr.search("scalable bgp", 5)
    assert len(res) == 2
    assert res[1].identifier_type == IdentifierType.arxiv and res[1].arxiv_id == "2405.12345" and res[1].year == 2024
    assert calls[-1].url.params["rows"] == "5"

    p = await cr.get_by_pii("S0142061525003874")
    assert p is not None and p.doi == "10.1016/j.ijepes.2025.110839"
    assert calls[-1].url.params["filter"] == "alternative-id:S0142061525003874"
    empty = CrossrefClient(client(lambda r: httpx.Response(200, json={"message": {"items": []}})))
    assert await empty.get_by_pii("S0142061525003874") is None


async def test_crossref_errors() -> None:
    with pytest.raises(UpstreamError):
        await CrossrefClient(client(lambda r: httpx.Response(500))).get("10.1/x")
    with pytest.raises(UpstreamError):
        await CrossrefClient(client(lambda r: httpx.Response(200, content=b"not json"))).search("x")
    with pytest.raises(UpstreamError):
        await CrossrefClient(client(lambda r: httpx.Response(200, json=[1]))).search("x")


async def test_s2_match() -> None:
    seen: list[httpx.Request] = []
    s2 = SemanticScholarClient(client(router(seen)), "secret")
    m = await s2.match("Scalable BGP Routing in Clos Fabrics")
    assert m is not None and m.identifier == "2405.12345" and m.identifier_type == IdentifierType.arxiv
    assert m.doi == "10.1145/3651890" and m.source == "semantic_scholar" and m.year == 2024
    assert m.authors[0].family == "Lovelace"
    assert "secret" not in str(seen[0].url)
    assert seen[0].headers["x-api-key"] == "secret"
    assert await SemanticScholarClient(client(lambda r: httpx.Response(404))).match("x") is None
    no_ids = {"data": [{"title": "T", "externalIds": {"CorpusId": 1}}]}
    assert await SemanticScholarClient(client(lambda r: httpx.Response(200, json=no_ids))).match("T") is None


async def test_resolver_arxiv_doi_pii() -> None:
    r = resolver(client(router()))
    res = await r.resolve("https://arxiv.org/abs/2405.12345v2")
    assert res.kind == "arxiv" and len(res.results) == 1
    assert res.results[0].bibtex.startswith("@misc{lovelace2024scalable,")
    res = await r.resolve("doi:10.1145/3651890")
    assert res.kind == "doi" and res.results[0].bibtex.startswith("@article{")
    res = await r.resolve("10.9999/missing")
    assert res.results == []
    res = await r.resolve("S0142-0615(25)00387-4")
    assert res.kind == "doi" and res.results[0].doi == "10.1016/j.ijepes.2025.110839"
    res = await r.resolve("10.48550/arXiv.2405.12345")
    assert res.kind == "arxiv"


async def test_resolver_title_merges_dedupes() -> None:
    res = await resolver(client(router())).resolve("Scalable BGP Routing in Clos Fabrics", limit=5)
    assert res.kind == "title"
    assert res.results[0].source == "semantic_scholar"
    # CrossRef hits for the same DOI / arXiv id are dropped as duplicates.
    assert len(res.results) == 1


async def test_resolver_validation_and_errors() -> None:
    r = resolver(client(router()))
    with pytest.raises(ValueError):
        await r.resolve("x" * 501)
    with pytest.raises(ValueError):
        await r.resolve("   ")
    with pytest.raises(UpstreamError):
        await resolver(client(lambda q: httpx.Response(500))).resolve("2405.12345")
    with pytest.raises(UpstreamError):
        await resolver(client(lambda q: httpx.Response(500))).resolve("some title words")


async def test_resolver_s2_failure_tolerated_not_cached() -> None:
    def handler(req):
        if req.url.host == "api.semanticscholar.org":
            return httpx.Response(429)
        return router()(req)

    redis = FakeRedis()
    res = await resolver(client(handler), redis).resolve("Scalable BGP Routing in Clos Fabrics")
    assert len(res.results) == 2
    assert redis.store == {}


async def test_resolver_cache() -> None:
    calls: list[httpx.Request] = []
    redis = FakeRedis()
    r = resolver(client(router(calls)), redis)
    first = await r.resolve("2405.12345")
    n = len(calls)
    assert list(redis.store) and next(iter(redis.store)).startswith("bf:lookup:")
    second = await r.resolve("arXiv:2405.12345v3")
    assert len(calls) == n
    assert second.results == first.results and second.query == "arXiv:2405.12345v3"
    await r.resolve("10.9999/missing")
    n = len(calls)
    await r.resolve("10.9999/missing")
    assert len(calls) == n  # empty results cached too


async def test_resolver_survives_broken_cache() -> None:
    res = await resolver(client(router()), BrokenRedis()).resolve("2405.12345")
    assert len(res.results) == 1


def test_crossref_unescapes_html_entities_and_markup():
    from app.services.metadata.crossref import to_metadata

    meta = to_metadata(
        {
            "DOI": "10.1016/J.IJEPES.2025.110839",
            "title": ["Power &amp; <i>Energy</i> H<sub>2</sub>"],
            "author": [{"family": "M&#252;ller", "given": "Ana"}],
            "container-title": ["International Journal of Electrical Power &amp; Energy Systems"],
            "issued": {"date-parts": [[2025]]},
        }
    )
    assert meta is not None
    assert meta.title == "Power & Energy H2"
    assert meta.authors[0].family == "Müller"
    assert meta.venue == "International Journal of Electrical Power & Energy Systems"
