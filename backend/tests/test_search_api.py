import uuid

from sqlalchemy import func, select, text

from app.db.session import get_sessionmaker
from app.models.chunk import PaperChunk
from tests.helpers import join_workspace, pdf_files
from tests.pdfgen import make_pdf_pages

API = "/api/v1"
BBOX = [{"x1": 50, "y1": 738, "x2": 200, "y2": 758, "width": 612, "height": 792}]


async def make_paper(c, title, chunks, workspace_id=None):
    """Wishlist paper plus hand-written chunks ``[(page, section, text)]``."""
    body = {"title": title}
    if workspace_id:
        body["workspace_id"] = workspace_id
    r = await c.post(f"{API}/papers/metadata-only", json=body)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    async with get_sessionmaker()() as db:
        for i, (page, section, body_text) in enumerate(chunks):
            db.add(
                PaperChunk(
                    paper_id=uuid.UUID(pid), chunk_index=i, page_number=page, section=section, text=body_text,
                    bboxes=BBOX,
                )
            )
        await db.commit()
    return pid


async def test_search_english_japanese_and_wildcards(login, resolver):
    _, a = await login("a@example.com")
    p1 = await make_paper(a, "Clos fabrics", [
        (1, "1 Intro", "BGP routing in Clos fabrics scales to thousands of switches."),
        (4, "3.2 Design", "Unrelated text about hashing."),
    ])
    p2 = await make_paper(a, "日本語の論文", [(2, None, "本論文ではデータセンターのルーティング設計を提案する。")])
    p3 = await make_paper(a, "Percent", [(1, None, "Achieves 100% reliable_links in tests.")])

    r = await a.get(f"{API}/search", params={"q": "routing Clos"})
    assert r.status_code == 200, r.text
    hits = r.json()
    assert hits[0]["paper_id"] == p1 and hits[0]["page_number"] == 1 and hits[0]["section"] == "1 Intro"
    assert hits[0]["bboxes"] == BBOX and hits[0]["title"] == "Clos fabrics"
    assert set(hits[0]) == {
        "paper_id", "workspace_id", "title", "year", "citekey", "chunk_id", "page_number", "section", "snippet",
        "bboxes", "score",
    }
    assert "Clos" in hits[0]["snippet"]

    # Japanese has no word boundaries: substring match
    hits = (await a.get(f"{API}/search", params={"q": "ルーティング"})).json()
    assert [h["paper_id"] for h in hits] == [p2]
    assert "ルーティング" in hits[0]["snippet"]

    # LIKE wildcards are literal
    assert [h["paper_id"] for h in (await a.get(f"{API}/search", params={"q": "100%"})).json()] == [p3]
    assert [h["paper_id"] for h in (await a.get(f"{API}/search", params={"q": "reliable_links"})).json()] == [p3]
    assert (await a.get(f"{API}/search", params={"q": "%%"})).json() == []

    # filters and validation
    only = (await a.get(f"{API}/search", params={"q": "text", "paper_id": p1})).json()
    assert {h["paper_id"] for h in only} == {p1}
    assert len((await a.get(f"{API}/search", params={"q": "in", "limit": 1})).json()) == 1
    assert (await a.get(f"{API}/search", params={"q": "x"})).status_code == 422
    assert (await a.get(f"{API}/search", params={"q": "ab", "limit": 51})).status_code == 422
    assert (await a.get(f"{API}/search", params={"q": "a" * 201})).status_code == 422
    assert (await a.get(f"{API}/search")).status_code == 422


async def test_search_snippet_window(login, resolver):
    _, a = await login("a@example.com")
    long = "x" * 1000 + " needle " + "y" * 1000
    await make_paper(a, "Long", [(1, None, long)])
    snip = (await a.get(f"{API}/search", params={"q": "needle"})).json()[0]["snippet"]
    assert "needle" in snip and snip.startswith("...") and snip.endswith("...") and len(snip) < 700


async def test_search_permissions(login, resolver, pat_client):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    _, v = await login("v@example.com")
    pa = await make_paper(a, "Private", [(1, None, "secret zebra findings")])
    ws = (await a.post(f"{API}/workspaces", json={"name": "Lab"})).json()
    ps = await make_paper(a, "Shared", [(3, None, "shared zebra findings")], workspace_id=ws["id"])
    await join_workspace(a, v, ws["id"], "v@example.com", "viewer")

    assert (await b.get(f"{API}/search", params={"q": "zebra"})).json() == []
    assert (await b.get(f"{API}/search", params={"q": "zebra", "paper_id": pa})).status_code == 404
    assert (await b.get(f"{API}/search", params={"q": "zebra", "workspace_id": ws["id"]})).status_code == 404

    assert {h["paper_id"] for h in (await a.get(f"{API}/search", params={"q": "zebra"})).json()} == {pa, ps}
    only_ws = (await a.get(f"{API}/search", params={"q": "zebra", "workspace_id": ws["id"]})).json()
    assert [h["paper_id"] for h in only_ws] == [ps]
    assert [h["paper_id"] for h in (await v.get(f"{API}/search", params={"q": "zebra"})).json()] == [ps]
    assert (await v.get(f"{API}/search", params={"q": "zebra", "paper_id": pa})).status_code == 404

    # scopes
    no_read = await pat_client(a, scopes=("write:papers",))
    assert (await no_read.get(f"{API}/search", params={"q": "zebra"})).status_code == 403
    ro = await pat_client(a, scopes=("read:papers",))
    assert len((await ro.get(f"{API}/search", params={"q": "zebra"})).json()) == 2
    anon = await pat_client(a)
    anon.headers.pop("Authorization")
    assert (await anon.get(f"{API}/search", params={"q": "zebra"})).status_code == 401


async def test_search_rate_limit(login, resolver, monkeypatch):
    from app.api.v1 import search

    monkeypatch.setattr(search, "SEARCH_RATE_LIMIT", 2)
    _, a = await login("a@example.com")
    for _ in range(2):
        assert (await a.get(f"{API}/search", params={"q": "zebra"})).status_code == 200
    r = await a.get(f"{API}/search", params={"q": "zebra"})
    assert r.status_code == 429 and "retry-after" in r.headers


# ---------------------------------------------------------------- chunks
async def test_chunks_endpoint(login, resolver, pat_client):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    pid = await make_paper(a, "P", [(1, "A", "one"), (1, "A", "one b"), (2, "B", "two"), (12, None, "twelve")])

    d = (await a.get(f"{API}/papers/{pid}/chunks")).json()
    assert d["page_from"] == 1 and d["page_to"] == 1 and d["index_status"] == "pending"
    assert [c["text"] for c in d["chunks"]] == ["one", "one b"]
    assert d["chunks"][0]["bboxes"] == BBOX and d["chunks"][0]["section"] == "A"

    d = (await a.get(f"{API}/papers/{pid}/chunks", params={"page_from": 1, "page_to": 2})).json()
    assert [c["text"] for c in d["chunks"]] == ["one", "one b", "two"]
    assert (await a.get(f"{API}/papers/{pid}/chunks", params={"page_from": 1, "page_to": 11})).status_code == 422
    assert (await a.get(f"{API}/papers/{pid}/chunks", params={"page_from": 3, "page_to": 2})).status_code == 422
    assert (await a.get(f"{API}/papers/{pid}/chunks", params={"page_from": 0})).status_code == 422
    assert (await a.get(f"{API}/papers/{pid}/chunks", params={"page_from": 3, "page_to": 4})).json()["chunks"] == []

    assert (await b.get(f"{API}/papers/{pid}/chunks")).status_code == 404
    assert (await a.get(f"{API}/papers/{uuid.uuid4()}/chunks")).status_code == 404
    wo = await pat_client(a, scopes=("write:papers",))
    assert (await wo.get(f"{API}/papers/{pid}/chunks")).status_code == 403


# ---------------------------------------------------------------- notes append
async def test_notes_append(login, resolver, pat_client):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    _, v = await login("v@example.com")
    ws = (await a.post(f"{API}/workspaces", json={"name": "Lab"})).json()
    await join_workspace(a, v, ws["id"], "v@example.com", "viewer")
    pid = await make_paper(a, "P", [], workspace_id=ws["id"])

    pat = await pat_client(a, scopes=("write:papers",))
    r = await pat.post(f"{API}/papers/{pid}/notes/append", json={"text": "first"})
    assert r.status_code == 200 and r.json()["notes_length"] == 5
    assert (await pat.post(f"{API}/papers/{pid}/notes/append", json={"text": "second"})).status_code == 200
    assert (await a.get(f"{API}/papers/{pid}")).json()["notes_md"] == "first\n\nsecond"

    # a UI edit in between is preserved because the append happens in SQL
    await a.patch(f"{API}/papers/{pid}", json={"notes_md": "edited in UI"})
    await pat.post(f"{API}/papers/{pid}/notes/append", json={"text": "third"})
    assert (await a.get(f"{API}/papers/{pid}")).json()["notes_md"] == "edited in UI\n\nthird"

    assert (await pat.post(f"{API}/papers/{pid}/notes/append", json={"text": ""})).status_code == 422
    assert (await pat.post(f"{API}/papers/{pid}/notes/append", json={"text": "x" * 20_001})).status_code == 422
    assert (await v.post(f"{API}/papers/{pid}/notes/append", json={"text": "hi"})).status_code == 404  # viewer
    assert (await b.post(f"{API}/papers/{pid}/notes/append", json={"text": "hi"})).status_code == 404
    ro = await pat_client(a, scopes=("read:papers",))
    assert (await ro.post(f"{API}/papers/{pid}/notes/append", json={"text": "hi"})).status_code == 403
    assert (await a.get(f"{API}/papers/{pid}")).json()["notes_md"] == "edited in UI\n\nthird"


async def test_notes_append_size_cap(login, resolver):
    _, a = await login("a@example.com")
    pid = await make_paper(a, "P", [])
    await a.patch(f"{API}/papers/{pid}", json={"notes_md": "n" * 498_000})
    assert (await a.post(f"{API}/papers/{pid}/notes/append", json={"text": "y" * 1000})).status_code == 200
    assert (await a.post(f"{API}/papers/{pid}/notes/append", json={"text": "y" * 1000})).status_code == 422


# ---------------------------------------------------------------- indexing end to end
async def test_process_paper_writes_chunks_and_search_finds_them(login, resolver, storage, enqueued):
    from app.workers.tasks import process_paper

    _, a = await login("a@example.com")
    data = make_pdf_pages(
        [[(20, "arXiv:2405.12345v1 [cs.NI]"), (10, "Scalable zebra routing in fabrics")], [(10, "second page zebra")]]
    )
    r = await a.post(f"{API}/papers/upload", files={"file": ("p.pdf", data, "application/pdf")})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]

    assert await process_paper({}, pid) == "ok"
    d = (await a.get(f"{API}/papers/{pid}")).json()
    assert d["index_status"] == "ready" and d["page_count"] == 2
    async with get_sessionmaker()() as db:
        n = (
            await db.execute(
                select(func.count()).select_from(PaperChunk).where(PaperChunk.paper_id == uuid.UUID(pid))
            )
        ).scalar_one()
        nulls = (await db.execute(text("select count(*) from paper_chunks where embedding is null"))).scalar_one()
    assert n == 2 and nulls == 2

    # re-running replaces rather than duplicates
    assert await process_paper({}, pid) == "ok"
    hits = (await a.get(f"{API}/search", params={"q": "zebra", "paper_id": pid})).json()
    assert sorted(h["page_number"] for h in hits) == [1, 2]
    assert hits[0]["bboxes"] and hits[0]["bboxes"][0]["width"] == 612

    # a file that cannot be parsed marks the paper failed
    path = pdf_files(storage.library)[0]
    path.write_bytes(b"%PDF-1.4\ngarbage")
    assert await process_paper({}, pid) == "failed"
    assert (await a.get(f"{API}/papers/{pid}")).json()["index_status"] == "failed"


async def test_reindex_cli(login, resolver, storage, enqueued, monkeypatch):
    from typer.testing import CliRunner

    from app import cli

    _, a = await login("a@example.com")
    data = make_pdf_pages([[(10, "arXiv:2405.12345v1 [cs.NI]")]])
    pid = (await a.post(f"{API}/papers/upload", files={"file": ("p.pdf", data, "application/pdf")})).json()["id"]
    wish = (await a.post(f"{API}/papers/metadata-only", json={"title": "wish"})).json()["id"]
    enqueued.jobs.clear()

    # `reindex` runs _reindex under asyncio.run; here the coroutine runs on the test loop, so the shared
    # engine and Arq pool must not be closed by it.
    async def _noop():
        return None

    from app.core import queue

    monkeypatch.setattr(queue, "close_arq_pool", _noop)
    monkeypatch.setattr(cli, "dispose_engine", _noop)

    assert await cli._reindex(None) == 1  # the wishlist entry has no PDF
    assert enqueued.jobs == [("process_paper", pid)]
    enqueued.jobs.clear()
    assert await cli._reindex(uuid.UUID(pid)) == 1 and enqueued.jobs == [("process_paper", pid)]
    assert await cli._reindex(uuid.UUID(wish)) == 0
    assert await cli._reindex(uuid.uuid4()) == 0

    runner = CliRunner()  # argument validation happens before any event loop starts
    assert runner.invoke(cli.cli, ["reindex"]).exit_code == 2
    assert runner.invoke(cli.cli, ["reindex", "--all", "--paper", pid]).exit_code == 2
