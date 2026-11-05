"""The embedding model arrives after startup: embed_paper waits instead of failing, a cron fills in the vectors,
and /search/status says where things stand."""

import uuid

import httpx
import pytest
from sqlalchemy import text

from app.core.config import get_settings
from app.core.queue import embed_job_id
from app.db.session import get_sessionmaker
from app.models.paper import IndexStatus, Paper
from app.services import embedding
from app.services.embedding import FakeEmbedder, ModelNotReady
from app.services.embedding.providers import LocalEmbedder
from app.workers.settings import WorkerSettings
from app.workers.tasks import BACKFILL_BATCH, backfill_embeddings, backfill_embeddings_core, embed_paper
from tests.helpers import FakePool, join_workspace
from tests.test_search_api import API, make_paper

MODEL = "fake@test"


async def indexed_paper(client, title, n_chunks=2, workspace_id=None):
    pid = await make_paper(client, title, [(1, None, f"{title} chunk {i}") for i in range(n_chunks)], workspace_id)
    async with get_sessionmaker()() as db:
        (await db.get(Paper, uuid.UUID(pid))).has_pdf = True
        await db.commit()
    return pid


async def embedded(pid):
    async with get_sessionmaker()() as db:
        rows = await db.execute(
            text("select embedding is not null from paper_chunks where paper_id = :p"), {"p": uuid.UUID(pid)}
        )
        return [r[0] for r in rows]


def local_embedder(state=None, healthz_status=200):
    """A LocalEmbedder whose embedder container says ``state`` on /healthz (None: not answering)."""

    def handler(request: httpx.Request) -> httpx.Response:
        if state is None:
            raise httpx.ConnectError("down")
        if request.url.path == "/healthz":
            return httpx.Response(healthz_status, json={"status": "ok", "model": state})
        return httpx.Response(503, json={"detail": "model not ready"})

    return LocalEmbedder("http://embedder:8001", "int8", transport=httpx.MockTransport(handler))


# ---------------------------------------------------------------- the client
async def test_local_embedder_503_is_model_not_ready_and_state_reads_healthz():
    with pytest.raises(ModelNotReady):
        await local_embedder("absent").embed(["a"], "document")
    for state in ("absent", "loading", "ready", "disabled"):
        assert await local_embedder(state).state() == state
    assert await local_embedder(None).state() == "unreachable"
    assert await local_embedder("weird").state() == "unreachable"
    assert await local_embedder("ready", healthz_status=500).state() == "unreachable"
    assert await embedding.model_state(None) == "disabled"
    assert await embedding.model_state(FakeEmbedder(MODEL)) == "ready"  # external providers: assumed ready


# ---------------------------------------------------------------- embed_paper
async def test_embed_paper_waits_without_using_a_retry_when_the_model_is_not_ready(login, enqueued, monkeypatch):
    _, a = await login("a@example.com")
    pid = await indexed_paper(a, "Waiting")
    monkeypatch.setattr(embedding, "get_embedder", lambda: local_embedder("absent"))
    for attempt in (1, 2, 3, 4):  # never Retry, never failed, however often it runs
        assert await embed_paper({"job_try": attempt}, pid) == "waiting"
    assert await embedded(pid) == [False, False]
    async with get_sessionmaker()() as db:
        assert (await db.get(Paper, uuid.UUID(pid))).index_status != IndexStatus.failed


async def test_embed_paper_with_provider_none_returns_without_embedding(login, enqueued):
    _, a = await login("a@example.com")
    pid = await indexed_paper(a, "Off")
    assert embedding.get_embedder() is None
    assert await embed_paper({}, pid) == "ok"
    assert await embedded(pid) == [False, False]


async def test_embed_paper_promotes_a_paper_left_processing_by_the_old_version(login, enqueued, monkeypatch):
    _, a = await login("a@example.com")
    pid = await indexed_paper(a, "Old")
    async with get_sessionmaker()() as db:
        (await db.get(Paper, uuid.UUID(pid))).index_status = IndexStatus.processing
        await db.commit()
    monkeypatch.setattr(embedding, "get_embedder", lambda: FakeEmbedder(MODEL))
    assert await embed_paper({}, pid) == "ok"
    async with get_sessionmaker()() as db:
        assert (await db.get(Paper, uuid.UUID(pid))).index_status == IndexStatus.ready


# ---------------------------------------------------------------- backfill cron
async def run_backfill(pool, embedder, state="ready"):
    return await backfill_embeddings_core(sessionmaker=get_sessionmaker(), pool=pool, embedder=embedder, state=state)


async def test_backfill_queues_papers_with_missing_vectors_once(login, enqueued):
    _, a = await login("a@example.com")
    p1 = await indexed_paper(a, "One")
    p2 = await indexed_paper(a, "Two")
    wish = (await a.post(f"{API}/papers/metadata-only", json={"title": "wish"})).json()["id"]  # no chunks, no pdf
    pool, fake = FakePool(), FakeEmbedder(MODEL)
    assert await run_backfill(pool, fake) == 2
    assert {args[1] for args in pool.jobs} == {p1, p2} and wish not in {args[1] for args in pool.jobs}
    assert pool.job_ids == {embed_job_id(p1), embed_job_id(p2)}
    assert await run_backfill(pool, fake) == 0  # already queued: Arq refuses the same job id
    assert len(pool.jobs) == 2
    # once p1 has its vectors it is left alone
    pool2 = FakePool()
    async with get_sessionmaker()() as db:
        await db.execute(
            text("update paper_chunks set embedding_model = :m, embedding = array_fill(0.5::real, ARRAY[1024])::vector "
                 "where paper_id = :p"),
            {"m": MODEL, "p": uuid.UUID(p1)},
        )
        await db.commit()
    assert await run_backfill(pool2, fake) == 1 and pool2.jobs == [("embed_paper", p2)]
    # a different model makes everything stale again
    pool3 = FakePool()
    assert await run_backfill(pool3, FakeEmbedder("other@model")) == 2


async def test_backfill_waits_for_a_ready_embedder_and_for_a_provider(login, enqueued):
    _, a = await login("a@example.com")
    await indexed_paper(a, "One")
    pool = FakePool()
    for state in ("absent", "loading", "unreachable", "disabled"):
        assert await run_backfill(pool, FakeEmbedder(MODEL), state) == 0
    assert await run_backfill(pool, None, "disabled") == 0
    assert pool.jobs == []


async def test_backfill_is_limited_per_run_and_fixes_old_processing_papers(login, enqueued, monkeypatch):
    from app.workers import tasks

    assert BACKFILL_BATCH >= 20
    monkeypatch.setattr(tasks, "BACKFILL_BATCH", 3)
    _, a = await login("a@example.com")
    pids = [await indexed_paper(a, f"P{i}", n_chunks=1) for i in range(5)]
    async with get_sessionmaker()() as db:
        await db.execute(text("update papers set index_status = 'processing'"))
        await db.commit()
    pool = FakePool()
    assert await run_backfill(pool, FakeEmbedder(MODEL)) == 3
    assert await run_backfill(pool, FakeEmbedder(MODEL)) == 0 and len(pool.jobs) == 3  # the same first three
    async with get_sessionmaker()() as db:
        rows = (await db.execute(text("select distinct index_status::text from papers"))).scalars().all()
    assert rows == ["ready"] and len(pids) == 5  # even with no embedder state: the text index is complete


async def test_backfill_task_asks_the_embedder_and_uses_the_arq_redis(login, enqueued, monkeypatch):
    _, a = await login("a@example.com")
    pid = await indexed_paper(a, "One")
    pool = FakePool()
    monkeypatch.setattr(embedding, "get_embedder", lambda: local_embedder("loading"))
    assert await backfill_embeddings({"redis": pool}) == 0
    monkeypatch.setattr(embedding, "get_embedder", lambda: local_embedder("ready"))
    assert await backfill_embeddings({"redis": pool}) == 1 and pool.jobs == [("embed_paper", pid)]


def test_worker_registers_the_cron_every_ten_minutes_and_embed_keeps_no_result():
    job = next(j for j in WorkerSettings.cron_jobs if j.coroutine is backfill_embeddings)
    assert job.minute == {0, 10, 20, 30, 40, 50} and job.second == {0} and job.unique
    embed = next(f for f in WorkerSettings.functions if getattr(f, "name", None) == "embed_paper")
    assert embed.keep_result_s == 0


# ---------------------------------------------------------------- /search/status
async def test_search_status_requires_login(make_client):
    assert (await make_client().get(f"{API}/search/status")).status_code == 401


async def test_search_status_disabled_by_default(login, enqueued):
    _, a = await login("a@example.com")
    await indexed_paper(a, "One", n_chunks=3)
    assert get_settings().embedding_provider == "none"
    r = await a.get(f"{API}/search/status")
    assert r.status_code == 200
    assert r.json() == {
        "provider": "none", "model_state": "disabled", "chunks_total": 3, "chunks_embedded": 0, "degraded": True,
    }


async def test_search_status_counts_only_my_workspaces_and_current_model(login, enqueued, monkeypatch):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    _, v = await login("v@example.com")
    mine = await indexed_paper(a, "Mine", n_chunks=2)
    await indexed_paper(b, "Theirs", n_chunks=5)
    ws = (await a.post(f"{API}/workspaces", json={"name": "Lab"})).json()
    await indexed_paper(a, "Shared", n_chunks=4, workspace_id=ws["id"])
    await join_workspace(a, v, ws["id"], "v@example.com", "viewer")

    monkeypatch.setattr(embedding, "get_embedder", lambda: FakeEmbedder(MODEL))
    async with get_sessionmaker()() as db:  # one of mine has this model's vector, another an old model's
        await db.execute(
            text("update paper_chunks set embedding_model = :m, embedding = array_fill(0.5::real, ARRAY[1024])::vector "
                 "where paper_id = :p and chunk_index = 0"),
            {"m": MODEL, "p": uuid.UUID(mine)},
        )
        await db.execute(
            text("update paper_chunks set embedding_model = 'old', embedding = array_fill(0.5::real, ARRAY[1024])::vector "
                 "where paper_id = :p and chunk_index = 1"),
            {"p": uuid.UUID(mine)},
        )
        await db.commit()
    status = lambda c: c.get(f"{API}/search/status")  # noqa: E731
    ra = (await status(a)).json()
    assert (ra["chunks_total"], ra["chunks_embedded"]) == (6, 1) and ra["model_state"] == "ready" and ra["degraded"] is False
    rb = (await status(b)).json()
    assert (rb["chunks_total"], rb["chunks_embedded"]) == (5, 0)  # nothing of a's, not even a count
    rv = (await status(v)).json()
    assert (rv["chunks_total"], rv["chunks_embedded"]) == (4, 0)  # only the shared workspace


async def test_search_status_follows_the_embedder_state(login, enqueued, monkeypatch):
    _, a = await login("a@example.com")
    monkeypatch.setattr(get_settings(), "embedding_provider", "local")
    for state, degraded in (("ready", False), ("loading", True), ("absent", True), (None, True)):
        monkeypatch.setattr(embedding, "get_embedder", lambda state=state: local_embedder(state))
        body = (await a.get(f"{API}/search/status")).json()
        assert body["provider"] == "local" and body["degraded"] is degraded
        assert body["model_state"] == (state or "unreachable")


def test_defaults_are_none(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("EMBEDDING_PROVIDER", raising=False)
    s = Settings(_env_file=None, database_url="postgresql+asyncpg://u:p@h/d", redis_url="redis://h", jwt_secret="x" * 40)
    assert s.embedding_provider == "none"
