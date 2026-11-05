"""Embedding jobs, the CLI, and hybrid search (keyword + substring + vector, fused with RRF)."""

import uuid

import pytest
from arq import Retry
from sqlalchemy import text

from app.api.v1.search import rrf
from app.core.config import get_settings
from app.db.session import get_sessionmaker
from app.models.chunk import PaperChunk
from app.models.paper import IndexStatus, Paper
from app.services import embedding
from app.services.embedding import EmbeddingError, FakeEmbedder
from app.workers.tasks import EMBED_MAX_TRIES, embed_paper, process_paper
from tests.pdfgen import make_pdf_pages
from tests.test_search_api import API, make_paper

MODEL = "fake@test"


@pytest.fixture
def fake(monkeypatch):
    embedder = FakeEmbedder(MODEL)
    monkeypatch.setattr(embedding, "get_embedder", lambda: embedder)
    return embedder


async def chunk_rows(pid):
    async with get_sessionmaker()() as db:
        rows = await db.execute(
            text("select chunk_index, embedding is not null as has, embedding_model from paper_chunks "
                 "where paper_id = :p order by chunk_index"),
            {"p": uuid.UUID(pid)},
        )
        return [tuple(r) for r in rows]


async def status(pid):
    async with get_sessionmaker()() as db:
        return (await db.get(Paper, uuid.UUID(pid))).index_status


# ---------------------------------------------------------------- RRF
def test_rrf_sums_reciprocal_ranks_and_records_matches():
    a, b, c = (uuid.uuid4() for _ in range(3))
    fused = rrf({"keyword": [a, b], "substring": [b], "semantic": [c, b, a]})
    assert fused[a] == (pytest.approx(1 / 61 + 1 / 63), ["keyword", "semantic"])
    assert fused[b] == (pytest.approx(1 / 62 + 1 / 61 + 1 / 62), ["keyword", "substring", "semantic"])
    assert fused[c] == (pytest.approx(1 / 61), ["semantic"])
    assert rrf({}) == {}


# ---------------------------------------------------------------- process_paper -> embed_paper
async def upload(a):
    data = make_pdf_pages([[(20, "arXiv:2405.12345v1 [cs.NI]"), (10, "Scalable zebra routing")], [(10, "second page")]])
    r = await a.post(f"{API}/papers/upload", files={"file": ("p.pdf", data, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_process_paper_is_ready_with_the_chunks_and_queues_embedding(login, resolver, storage, enqueued, fake):
    _, a = await login("a@example.com")
    pid = await upload(a)
    enqueued.jobs.clear()
    assert await process_paper({}, pid) == "ok"
    assert enqueued.jobs == [("embed_paper", pid)]
    assert await status(pid) == IndexStatus.ready  # embeddings are tracked apart (/search/status)
    # keyword search already works
    assert (await a.get(f"{API}/search", params={"q": "zebra", "mode": "keyword"})).json()


async def test_process_paper_stays_ready_when_the_job_cannot_be_queued(login, resolver, storage, enqueued, fake):
    _, a = await login("a@example.com")
    pid = await upload(a)

    async def broken(*args, **kw):
        raise ConnectionError("redis down")

    enqueued.enqueue_job = broken
    assert await process_paper({}, pid) == "ok"
    assert await status(pid) == IndexStatus.ready  # the backfill cron embeds it later
    assert len(await chunk_rows(pid)) == 2  # chunks stay: keyword search keeps working


async def test_embed_paper_writes_vectors_and_marks_ready(login, resolver, storage, enqueued, fake):
    _, a = await login("a@example.com")
    pid = await upload(a)
    await process_paper({}, pid)
    assert await embed_paper({}, pid) == "ok"
    assert all(has and model == MODEL for _, has, model in await chunk_rows(pid))
    assert await status(pid) == IndexStatus.ready
    calls = len(fake.calls)
    assert await embed_paper({}, pid) == "ok"  # nothing stale: no provider call
    assert len(fake.calls) == calls


async def test_embed_paper_batches_of_eight(login, resolver, storage, enqueued, fake):
    _, a = await login("a@example.com")
    pid = await make_paper(a, "Many", [(1, None, f"chunk number {i} text") for i in range(20)])
    async with get_sessionmaker()() as db:
        paper = await db.get(Paper, uuid.UUID(pid))
        paper.has_pdf = True
        await db.commit()
    assert await embed_paper({}, pid) == "ok"
    assert [n for _, n in fake.calls] == [8, 8, 4] and {k for k, _ in fake.calls} == {"document"}
    assert all(row[1] for row in await chunk_rows(pid))


async def test_embed_paper_only_redoes_chunks_from_another_model(login, resolver, storage, enqueued, fake):
    _, a = await login("a@example.com")
    pid = await make_paper(a, "Two", [(1, None, "first chunk"), (2, None, "second chunk")])
    async with get_sessionmaker()() as db:
        (await db.get(Paper, uuid.UUID(pid))).has_pdf = True
        await db.commit()
    await embed_paper({}, pid)
    async with get_sessionmaker()() as db:
        await db.execute(text("update paper_chunks set embedding_model = 'old-model' where chunk_index = 1"))
        await db.commit()
    fake.calls.clear()
    await embed_paper({}, pid)
    assert fake.calls == [("document", 1)]
    assert [m for _, _, m in await chunk_rows(pid)] == [MODEL, MODEL]


async def test_embed_paper_retries_then_fails_but_keeps_chunks(login, resolver, storage, enqueued, monkeypatch):
    class Down:
        model_id = MODEL

        async def embed(self, texts, kind, timeout=None):
            raise EmbeddingError("embedder: ConnectError")

    monkeypatch.setattr(embedding, "get_embedder", lambda: Down())
    _, a = await login("a@example.com")
    pid = await upload(a)
    await process_paper({}, pid)
    for attempt in range(1, EMBED_MAX_TRIES):
        with pytest.raises(Retry) as r:
            await embed_paper({"job_try": attempt}, pid)
        assert r.value.defer_score > 0
        assert await status(pid) == IndexStatus.ready
    assert await embed_paper({"job_try": EMBED_MAX_TRIES}, pid) == "failed"
    assert await status(pid) == IndexStatus.ready  # the text index is fine; only vectors are missing
    assert len(await chunk_rows(pid)) == 2
    assert not any(has for _, has, _ in await chunk_rows(pid))


async def test_embed_paper_without_provider_is_ready_and_skips_unknown(login, resolver, storage, enqueued):
    _, a = await login("a@example.com")
    pid = await upload(a)
    await process_paper({}, pid)  # provider none: ready right away, nothing queued
    assert await status(pid) == IndexStatus.ready and ("embed_paper", pid) not in enqueued.jobs
    assert await embed_paper({}, pid) == "ok" and await status(pid) == IndexStatus.ready
    assert await embed_paper({}, str(uuid.uuid4())) == "skipped"
    wish = (await a.post(f"{API}/papers/metadata-only", json={"title": "wish"})).json()["id"]
    assert await embed_paper({}, wish) == "skipped"


async def test_embed_paper_detects_chunks_replaced_meanwhile(login, resolver, storage, enqueued, monkeypatch):
    _, a = await login("a@example.com")
    pid = await make_paper(a, "Race", [(1, None, "one chunk")])
    async with get_sessionmaker()() as db:
        (await db.get(Paper, uuid.UUID(pid))).has_pdf = True
        await db.commit()

    class Racy(FakeEmbedder):
        async def embed(self, texts, kind, timeout=None):
            async with get_sessionmaker()() as db:  # process_paper ran again: the chunk rows are new
                await db.execute(text("delete from paper_chunks"))
                db.add(PaperChunk(paper_id=uuid.UUID(pid), chunk_index=0, page_number=1, text="new chunk", bboxes=[]))
                await db.commit()
            return await super().embed(texts, kind, timeout)

    monkeypatch.setattr(embedding, "get_embedder", lambda: Racy(MODEL))
    with pytest.raises(Retry):
        await embed_paper({}, pid)
    assert await status(pid) != IndexStatus.failed  # untouched
    monkeypatch.setattr(embedding, "get_embedder", lambda: FakeEmbedder(MODEL))
    assert await embed_paper({}, pid) == "ok" and all(h for _, h, _ in await chunk_rows(pid))


# ---------------------------------------------------------------- reindex CLI
async def test_reindex_embeddings_only_and_missing(login, resolver, storage, enqueued, fake, monkeypatch):
    from typer.testing import CliRunner

    from app import cli
    from app.core import queue

    async def _noop():
        return None

    monkeypatch.setattr(queue, "close_arq_pool", _noop)
    monkeypatch.setattr(cli, "dispose_engine", _noop)
    _, a = await login("a@example.com")
    p1 = await upload(a)
    await process_paper({}, p1)
    await a.post(f"{API}/papers/metadata-only", json={"title": "wishlist only"})
    enqueued.jobs.clear()

    assert await cli._reindex(None, embeddings_only=True, missing=True) == 1
    assert enqueued.jobs == [("embed_paper", p1)]
    await embed_paper({}, p1)
    enqueued.jobs.clear()
    assert await cli._reindex(None, embeddings_only=True, missing=True) == 0  # everything is current
    assert await cli._reindex(None, embeddings_only=True) == 1 and enqueued.jobs == [("embed_paper", p1)]
    assert await cli._reindex(uuid.UUID(p1), embeddings_only=True, missing=True) == 0

    # another model is configured: all chunks are stale again
    monkeypatch.setattr(embedding, "get_embedder", lambda: FakeEmbedder("other@model"))
    assert await cli._reindex(None, embeddings_only=True, missing=True) == 1

    enqueued.jobs.clear()
    assert await cli._reindex(None) == 1 and enqueued.jobs == [("process_paper", p1)]  # unchanged behaviour

    monkeypatch.setattr(embedding, "get_embedder", lambda: None)
    with pytest.raises(ValueError, match="none"):
        await cli._reindex(None, embeddings_only=True)

    runner = CliRunner()
    assert runner.invoke(cli.cli, ["reindex", "--all", "--missing"]).exit_code == 2


# ---------------------------------------------------------------- search
async def embed_all(*pids):
    for pid in pids:
        async with get_sessionmaker()() as db:
            (await db.get(Paper, uuid.UUID(pid))).has_pdf = True
            await db.commit()
        assert await embed_paper({}, pid) == "ok"


async def test_hybrid_semantic_and_matched(login, resolver, storage, enqueued, fake):
    _, a = await login("a@example.com")
    pid = await make_paper(a, "Routing", [
        (1, "1 Intro", "alpha beta gamma delta"),
        (2, None, "completely different words here"),
        (3, None, "the zebra crossing is striped"),
    ])
    await embed_all(pid)

    # words in a different order and one extra word: no AND match, no substring, but close in vector space
    r = await a.get(f"{API}/search", params={"q": "gamma alpha beta delta epsilon"})
    assert r.status_code == 200 and "x-search-degraded" not in r.headers
    hits = r.json()
    assert hits[0]["page_number"] == 1 and hits[0]["matched"] == ["semantic"]
    assert set(hits[0]) >= {"score", "matched", "bboxes", "snippet"}

    # an exact phrase is found by all three
    hits = (await a.get(f"{API}/search", params={"q": "zebra crossing"})).json()
    assert hits[0]["page_number"] == 3 and hits[0]["matched"] == ["keyword", "substring", "semantic"]
    assert hits[0]["score"] == pytest.approx(3 / 61, abs=1e-4)

    # modes
    kw = (await a.get(f"{API}/search", params={"q": "gamma alpha beta delta epsilon", "mode": "keyword"})).json()
    assert kw == []
    sem = (await a.get(f"{API}/search", params={"q": "zebra crossing", "mode": "semantic"})).json()
    assert sem[0]["page_number"] == 3 and all(h["matched"] == ["semantic"] for h in sem)
    n = len(fake.calls)
    await a.get(f"{API}/search", params={"q": "zebra crossing", "mode": "keyword"})
    assert len(fake.calls) == n  # keyword mode never calls the embedder
    assert fake.calls[-1][0] == "query"
    assert (await a.get(f"{API}/search", params={"q": "zebra", "mode": "bogus"})).status_code == 422


async def test_semantic_candidates_respect_permissions_and_filters(login, resolver, storage, enqueued, fake):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    secret = await make_paper(b, "Secret", [(1, None, "classified quokka research")])
    mine = await make_paper(a, "Mine", [(1, None, "public quokka notes")])
    await embed_all(secret, mine)
    for mode in ("semantic", "hybrid"):
        hits = (await a.get(f"{API}/search", params={"q": "classified quokka research", "mode": mode})).json()
        assert {h["paper_id"] for h in hits} == {mine}
    # a paper id of someone else's paper is a 404, in every mode
    for mode in ("semantic", "hybrid", "keyword"):
        r = await a.get(f"{API}/search", params={"q": "quokka", "mode": mode, "paper_id": secret})
        assert r.status_code == 404
    only = (await a.get(f"{API}/search", params={"q": "quokka", "mode": "semantic", "paper_id": mine})).json()
    assert [h["paper_id"] for h in only] == [mine]


async def test_vectors_from_another_model_are_not_used(login, resolver, storage, enqueued, fake):
    _, a = await login("a@example.com")
    pid = await make_paper(a, "P", [(1, None, "alpha beta gamma")])
    await embed_all(pid)
    assert (await a.get(f"{API}/search", params={"q": "alpha beta gamma", "mode": "semantic"})).json()
    async with get_sessionmaker()() as db:
        await db.execute(text("update paper_chunks set embedding_model = 'old-model'"))
        await db.commit()
    r = await a.get(f"{API}/search", params={"q": "alpha beta gamma", "mode": "semantic"})
    assert r.json() == [] and "x-search-degraded" not in r.headers  # not degraded: simply nothing to compare
    hybrid = (await a.get(f"{API}/search", params={"q": "alpha beta gamma"})).json()
    assert hybrid and hybrid[0]["matched"] == ["keyword", "substring"]


async def test_min_similarity_floor(login, resolver, storage, enqueued, fake, monkeypatch):
    _, a = await login("a@example.com")
    pid = await make_paper(a, "P", [(1, None, "alpha beta gamma delta")])
    await embed_all(pid)
    params = {"q": "alpha unrelated words", "mode": "semantic"}
    assert (await a.get(f"{API}/search", params=params)).json()
    monkeypatch.setattr(get_settings(), "search_min_similarity", 0.95)
    assert (await a.get(f"{API}/search", params=params)).json() == []


async def test_degraded_when_embedder_is_down_or_off(login, resolver, storage, enqueued, monkeypatch):
    _, a = await login("a@example.com")
    pid = await make_paper(a, "P", [(1, None, "the zebra crossing"), (2, None, "other")])

    class Down:
        model_id = MODEL

        async def embed(self, texts, kind, timeout=None):
            assert timeout == get_settings().embedding_query_timeout
            raise EmbeddingError("embedder: timed out")

    for factory in (lambda: Down(), lambda: None):
        monkeypatch.setattr(embedding, "get_embedder", factory)
        for mode in ("hybrid", "semantic"):
            r = await a.get(f"{API}/search", params={"q": "zebra crossing", "mode": mode})
            assert r.status_code == 200 and r.headers["x-search-degraded"] == "semantic-unavailable"
            assert [h["paper_id"] for h in r.json()] == [pid]
            assert r.json()[0]["matched"] == ["keyword", "substring"]
        r = await a.get(f"{API}/search", params={"q": "zebra crossing", "mode": "keyword"})
        assert "x-search-degraded" not in r.headers


async def test_search_results_keep_the_old_fields_and_order(login, resolver, storage, enqueued):
    _, a = await login("a@example.com")
    await make_paper(a, "P", [(1, None, "needle in a haystack"), (2, None, "haystack only")])
    hits = (await a.get(f"{API}/search", params={"q": "needle haystack"})).json()
    assert [h["page_number"] for h in hits] == [1] and hits[0]["matched"] == ["keyword"]  # not a phrase
    assert hits[0]["score"] == pytest.approx(1 / 61, abs=1e-4)
    assert set(hits[0]) == {
        "paper_id", "workspace_id", "title", "year", "citekey", "chunk_id", "page_number", "section", "snippet",
        "bboxes", "score", "matched",
    }
