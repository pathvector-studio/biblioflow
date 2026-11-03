"""Regression tests for the Phase 2 review findings."""

import asyncio
import hashlib

import httpcore
import pytest
from starlette.requests import Request

from app.core import net
from app.core.ratelimit import enforce_rate_limit
from app.core.redis import get_redis
from app.db.sql import escape_like
from app.services import ingest
from app.services.metadata.resolver import CACHE_TTL, EMPTY_CACHE_TTL, DefaultResolver
from app.workers import tasks
from tests.helpers import FakePool, make_meta, pdf_files
from tests.pdfgen import make_pdf
from tests.test_upload import ARXIV_LINES, REL, upload
from tests.test_watch import LINES, put, scan, slug_of
from tests.test_papers_api import add_wishlist

API = "/api/v1"


# ---------------------------------------------------------------- 2: citekey race
async def test_citekey_collision_is_retried(login, resolver, storage, enqueued, monkeypatch):
    _, c = await login("a@example.com")
    taken = (await add_wishlist(c, "Scalable BGP Routing in Clos Fabrics", authors=[{"family": "Smith", "given": "A"}], year=2024))["citekey"]
    real = ingest.unique_citekey
    calls = []

    async def racy(db, workspace_id, meta, exclude_id=None):
        calls.append(1)
        return taken if len(calls) == 1 else await real(db, workspace_id, meta, exclude_id)

    monkeypatch.setattr(ingest, "unique_citekey", racy)
    r = await upload(c, make_pdf(ARXIV_LINES))
    assert r.status_code == 201, r.text
    assert r.json()["citekey"] not in (taken, None) and len(calls) == 2
    assert pdf_files(storage.library) == [storage.library / REL]


async def test_metadata_only_citekey_collision_is_retried(login, resolver, monkeypatch):
    _, c = await login("a@example.com")
    taken = (await add_wishlist(c, "Alpha study", year=2020))["citekey"]
    real = ingest.unique_citekey
    calls = []

    async def racy(db, workspace_id, meta, exclude_id=None):
        calls.append(1)
        return taken if len(calls) == 1 else await real(db, workspace_id, meta, exclude_id)

    monkeypatch.setattr(ingest, "unique_citekey", racy)
    r = await c.post(f"{API}/papers/metadata-only", json={"title": "Alpha study", "year": 2020})
    assert r.status_code == 201 and r.json()["citekey"] != taken


async def test_identifier_duplicate_still_409(login, resolver, enqueued, storage):
    _, c = await login("a@example.com")
    assert (await upload(c, make_pdf(ARXIV_LINES))).status_code == 201
    assert (await upload(c, make_pdf(ARXIV_LINES, nonce="x"))).status_code == 409


# ---------------------------------------------------------------- 3: PDF-text identifiers are verified
async def test_cited_doi_of_other_paper_is_ignored(login, resolver, storage, enqueued):
    resolver.add(make_meta("10.1000/cited.1", title="Quantum Chromodynamics Lattice Survey", year=2001))
    _, c = await login("a@example.com")
    r = await upload(c, make_pdf(["An Entirely Different Study of Networks", "see doi:10.1000/cited.1"]))
    assert r.status_code == 201, r.text
    assert r.json()["identifier_type"] == "internal" and r.json()["identifier"] != "10.1000/cited.1"


async def test_own_doi_with_title_on_first_page_resolves(login, resolver, storage, enqueued):
    resolver.add(make_meta("10.1000/own.1", title="Scalable BGP Routing in Clos Fabrics"))
    _, c = await login("a@example.com")
    r = await upload(c, make_pdf(["Scalable BGP Routing in Clos Fabrics", "doi:10.1000/own.1"]))
    assert r.status_code == 201, r.text
    assert r.json()["identifier"] == "10.1000/own.1" and r.json()["identifier_type"] == "doi"


async def test_unverified_arxiv_stamp_falls_through_to_doi(login, resolver, storage, enqueued):
    resolver.add(make_meta("2405.12345", title="Totally Unrelated Cited Work"))
    resolver.add(make_meta("10.1000/own.2", title="Scalable BGP Routing in Clos Fabrics"))
    _, c = await login("a@example.com")
    r = await upload(c, make_pdf(["Scalable BGP Routing in Clos Fabrics", "arXiv:2405.12345v1 [cs.NI]", "doi:10.1000/own.2"]))
    assert r.status_code == 201 and r.json()["identifier"] == "10.1000/own.2"


# ---------------------------------------------------------------- 4: content type
@pytest.mark.parametrize("ctype", [None, "", "application/octet-stream", "application/x-pdf", "application/pdf; x=1"])
async def test_upload_accepts_pdf_content_types(login, resolver, storage, enqueued, ctype):
    _, c = await login("a@example.com")
    files = {"file": ("p.pdf", make_pdf(ARXIV_LINES), ctype)} if ctype is not None else {"file": ("p.pdf", make_pdf(ARXIV_LINES))}
    if ctype is None:  # httpx guesses from the name; force a missing header via a raw multipart body
        body = (
            b'--B\r\nContent-Disposition: form-data; name="file"; filename="p.pdf"\r\n\r\n' + make_pdf(ARXIV_LINES) + b"\r\n--B--\r\n"
        )
        r = await c.post(f"{API}/papers/upload", content=body, headers={"content-type": "multipart/form-data; boundary=B"})
    else:
        r = await c.post(f"{API}/papers/upload", files=files)
    assert r.status_code == 201, r.text


@pytest.mark.parametrize("ctype", ["text/plain", "text/html", "image/png"])
async def test_upload_rejects_other_content_types(login, resolver, storage, enqueued, ctype):
    _, c = await login("a@example.com")
    r = await upload(c, make_pdf(ARXIV_LINES), ctype=ctype)
    assert r.status_code == 415
    assert pdf_files(storage.library) == []


# ---------------------------------------------------------------- 5: rate limit before body parsing
async def test_upload_rate_limited_before_parsing(login, resolver, storage, monkeypatch):
    user, c = await login("a@example.com")
    for _ in range(60):
        await enforce_rate_limit(get_redis(), "papers_upload", (str(user.id),), 60)
    parsed = []
    orig = Request.form

    def spy(self, *a, **kw):
        parsed.append(1)
        return orig(self, *a, **kw)

    monkeypatch.setattr(Request, "form", spy)
    r = await upload(c, make_pdf(ARXIV_LINES))
    assert r.status_code == 429 and parsed == []


async def test_upload_workspace_in_form_is_access_checked(login, resolver, storage, enqueued):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    wid = next(w["id"] for w in (await b.get(f"{API}/workspaces")).json() if w["kind"] == "personal")
    r = await upload(a, make_pdf(ARXIV_LINES), workspace_id=wid)
    assert r.status_code == 404
    r = await a.post(f"{API}/papers/upload", params={"workspace_id": wid}, files={"file": ("p.pdf", make_pdf(ARXIV_LINES), "application/pdf")})
    assert r.status_code == 404


# ---------------------------------------------------------------- 6: author search
async def test_light_q_does_not_match_json_syntax(login, resolver):
    _, c = await login("a@example.com")
    p = await add_wishlist(c, "Clos fabrics", authors=[{"family": "Zhang", "given": "Wei"}])
    await add_wishlist(c, "Other topic", authors=[{"family": "Smith", "given": "Ann"}])

    async def ids(q):
        r = await c.get(f"{API}/papers/light", params={"q": q})
        assert r.status_code == 200
        return {x["id"] for x in r.json()}

    for q in ("family", "given", '"', "{", "Zhang\":"):
        assert await ids(q) == set(), q
    assert await ids("zhang") == {p["id"]}
    assert await ids("wei") == {p["id"]}
    assert await ids("Wei Zhang") == {p["id"]}


# ---------------------------------------------------------------- 7: connect fallback across validated IPs
async def test_backend_falls_back_to_next_validated_address(monkeypatch):
    async def fake_getaddrinfo(host, port, **kw):
        import socket

        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, port)) for a in ("192.0.2.1", "192.0.2.2")]

    monkeypatch.setattr(asyncio.get_running_loop(), "getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(net, "check_ip", lambda ip: None)
    backend = net.SafeNetworkBackend()
    tried = []

    async def fake_connect(host, port, **kw):
        tried.append(host)
        if host == "192.0.2.1":
            raise httpcore.ConnectError("down")
        return "stream"

    monkeypatch.setattr(backend._inner, "connect_tcp", fake_connect)
    assert await backend.connect_tcp("multi.test", 80) == "stream"
    assert tried == ["192.0.2.1", "192.0.2.2"]

    async def always_fail(host, port, **kw):
        raise httpcore.ConnectError(f"down {host}")

    monkeypatch.setattr(backend._inner, "connect_tcp", always_fail)
    with pytest.raises(httpcore.ConnectError, match="192.0.2.2"):
        await backend.connect_tcp("multi.test", 80)


# ---------------------------------------------------------------- 8: watch failure classification
async def test_watch_transient_failure_retries_then_rejects(login, resolver, storage, monkeypatch):
    _, c = await login("a@example.com")
    slug, _ = await slug_of(c)
    pool = FakePool()
    src = await put(storage, slug, "flaky.pdf", make_pdf(LINES))

    async def boom(*a, **kw):
        raise RuntimeError("db down")

    monkeypatch.setattr(tasks, "_ingest_one", boom)
    await scan(storage, resolver, pool)  # first sighting
    for _ in range(tasks.MAX_ATTEMPTS - 1):
        res = await scan(storage, resolver, pool)  # processed -> fails -> stability entry cleared
        assert res.rejected == [] and res.retried == [str(src)] and src.exists()
        assert (await scan(storage, resolver, pool)).retried == []  # re-seen, not processed yet
    res = await scan(storage, resolver, pool)
    assert res.rejected == [str(src)] and not src.exists()
    rej = list((storage.watch / "_rejected" / slug).glob("*.error.txt"))
    assert len(rej) == 1 and "db down" in rej[0].read_text() and "5 attempts" in rej[0].read_text()
    assert await get_redis().get(tasks.ATTEMPT_PREFIX + hashlib.sha256(str(src).encode()).hexdigest()) is None


async def test_watch_transient_then_success(login, resolver, storage, enqueued, monkeypatch):
    _, c = await login("a@example.com")
    slug, _ = await slug_of(c)
    src = await put(storage, slug, "ok.pdf", make_pdf(LINES))
    real = tasks._ingest_one
    state = {"n": 0}

    async def flaky(*a, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise OSError("disk hiccup")
        return await real(*a, **kw)

    monkeypatch.setattr(tasks, "_ingest_one", flaky)
    await scan(storage, resolver, enqueued)
    assert (await scan(storage, resolver, enqueued)).retried == [str(src)] and src.exists()
    await scan(storage, resolver, enqueued)
    assert (await scan(storage, resolver, enqueued)).ingested == [str(src)]
    assert not (storage.watch / "_rejected").exists()


async def test_watch_permanent_failure_rejected_immediately(login, resolver, storage):
    _, c = await login("a@example.com")
    slug, _ = await slug_of(c)
    pool = FakePool()
    src = await put(storage, slug, "bad.pdf", b"not a pdf at all")
    await scan(storage, resolver, pool)
    res = await scan(storage, resolver, pool)
    assert res.rejected == [str(src)] and res.retried == [] and not src.exists()


# ---------------------------------------------------------------- 9: negative cache TTL
class _Redis:
    def __init__(self):
        self.sets = []

    async def get(self, key):
        return None

    async def set(self, key, value, ex=None):
        self.sets.append(ex)


class _Stub:
    def __init__(self, meta):
        self.meta = meta

    async def get(self, _):
        return self.meta


async def test_empty_results_cached_briefly():
    redis = _Redis()
    r = DefaultResolver(_Stub(None), _Stub(None), None, redis)
    assert (await r.resolve("2405.12345")).results == []
    r2 = DefaultResolver(_Stub(make_meta()), _Stub(None), None, redis)
    assert len((await r2.resolve("2405.12345")).results) == 1
    assert redis.sets == [EMPTY_CACHE_TTL, CACHE_TTL] and EMPTY_CACHE_TTL < CACHE_TTL


# ---------------------------------------------------------------- 10
def test_escape_like():
    assert escape_like(r"a%b_c\d") == r"a\%b\_c\\d"
