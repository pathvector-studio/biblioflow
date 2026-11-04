import uuid

import pytest
from sqlalchemy import select, update

from app.db.session import get_sessionmaker
from app.models import Collection, Paper, Workspace
from app.schemas.metadata import UpstreamError
from tests.helpers import join_workspace, pdf_files
from tests.pdfgen import make_pdf

API = "/api/v1"
LIGHT_KEYS = {
    "id", "identifier", "citekey", "title", "authors", "year", "venue", "tags", "status", "has_pdf",
    "relative_file_path",
}


async def personal_ws(c) -> str:
    return next(w["id"] for w in (await c.get(f"{API}/workspaces")).json() if w["kind"] == "personal")


async def add_wishlist(c, title, **kw):
    r = await c.post(f"{API}/papers/metadata-only", json={"title": title, **kw})
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- /light
async def test_light_shape_filters_pagination(login, resolver):
    _, c = await login("a@example.com")
    p1 = await add_wishlist(c, "Clos fabrics routing", authors=[{"family": "Zhang", "given": "Wei"}], year=2023, tags=["net", "BGP"])
    p2 = await add_wishlist(c, "100% reliable_links", year=2024, tags=["bgp"])
    p3 = await add_wishlist(c, "Something else", year=2024)

    r = await c.get(f"{API}/papers/light")
    assert r.status_code == 200 and r.headers["X-Total-Count"] == "3"
    rows = r.json()
    assert len(rows) == 3
    assert all(set(row) == LIGHT_KEYS for row in rows)
    assert [row["id"] for row in rows] == [p3["id"], p2["id"], p1["id"]]  # newest first
    by_id = {row["id"]: row for row in rows}
    assert by_id[p1["id"]]["authors"] == [{"family": "Zhang", "given": "Wei"}]
    assert sorted(by_id[p1["id"]]["tags"], key=str.lower) == ["BGP", "net"]
    assert by_id[p1["id"]]["has_pdf"] is False and by_id[p1["id"]]["relative_file_path"] is None
    assert by_id[p1["id"]]["status"] == "wishlist"

    async def ids(**params):
        resp = await c.get(f"{API}/papers/light", params=params)
        assert resp.status_code == 200, resp.text
        return {row["id"] for row in resp.json()}, resp.headers["X-Total-Count"]

    assert (await ids(q="zhang"))[0] == {p1["id"]}  # authors
    assert (await ids(q="CLOS"))[0] == {p1["id"]}
    assert (await ids(q=p1["citekey"]))[0] == {p1["id"]}
    assert (await ids(q=p2["identifier"]))[0] == {p2["id"]}
    assert (await ids(q="%"))[0] == {p2["id"]}  # wildcard escaped
    assert (await ids(q="_"))[0] == {p2["id"]}
    assert (await ids(tag="bgp"))[0] == {p1["id"], p2["id"]}
    assert (await ids(tag="net"))[0] == {p1["id"]}
    assert (await ids(tag="nope"))[0] == set()
    assert (await ids(year=2024))[0] == {p2["id"], p3["id"]}
    assert (await ids(status="wishlist"))[1] == "3"
    assert (await ids(status="read"))[0] == set()

    page, total = await ids(limit=2, offset=0)
    assert len(page) == 2 and total == "3"
    rest, _ = await ids(limit=2, offset=2)
    assert len(rest) == 1 and rest.isdisjoint(page)

    for bad in ({"limit": 0}, {"limit": 201}, {"offset": -1}, {"status": "bogus"}, {"year": 0}):
        assert (await c.get(f"{API}/papers/light", params=bad)).status_code == 422


async def test_light_collection_filter(login, resolver):
    user, c = await login("a@example.com")
    p1 = await add_wishlist(c, "In collection")
    await add_wishlist(c, "Not in collection")
    async with get_sessionmaker()() as db:
        col = Collection(workspace_id=uuid.UUID(p1["workspace_id"]), name="Reading")
        db.add(col)
        await db.commit()
        cid = str(col.id)
    r = await c.patch(f"{API}/papers/{p1['id']}", json={"collection_ids": [cid]})
    assert r.status_code == 200 and r.json()["collection_ids"] == [cid]
    r = await c.get(f"{API}/papers/light", params={"collection_id": cid})
    assert [row["id"] for row in r.json()] == [p1["id"]] and r.headers["X-Total-Count"] == "1"


async def test_light_idor(login, resolver):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    pa = await add_wishlist(a, "Secret of A")
    await add_wishlist(b, "Paper of B")
    rows = (await b.get(f"{API}/papers/light")).json()
    assert [r["title"] for r in rows] == ["Paper of B"]
    assert (await b.get(f"{API}/papers/light", params={"q": "Secret"})).json() == []
    r = await b.get(f"{API}/papers/light", params={"workspace_id": pa["workspace_id"]})
    assert r.status_code == 404
    assert (await b.get(f"{API}/papers/light", params={"workspace_id": str(uuid.uuid4())})).status_code == 404
    # explicit own workspace works
    r = await b.get(f"{API}/papers/light", params={"workspace_id": await personal_ws(b)})
    assert r.status_code == 200 and len(r.json()) == 1


async def test_light_shared_workspace_visible_to_members(login, resolver):
    _, a = await login("a@example.com")
    _, v = await login("v@example.com")
    ws = (await a.post(f"{API}/workspaces", json={"name": "Lab"})).json()
    await join_workspace(a, v, ws["id"], "v@example.com", "viewer")
    p = (await a.post(f"{API}/papers/metadata-only", json={"title": "Shared paper", "workspace_id": ws["id"]})).json()
    assert [r["id"] for r in (await v.get(f"{API}/papers/light")).json()] == [p["id"]]
    assert (await v.get(f"{API}/papers/{p['id']}")).status_code == 200
    # viewer cannot write
    assert (await v.patch(f"{API}/papers/{p['id']}", json={"status": "read"})).status_code == 404
    r = await v.post(f"{API}/papers/metadata-only", json={"title": "x", "workspace_id": ws["id"]})
    assert r.status_code == 404


async def test_pat_scopes(login, pat_client, resolver):
    _, c = await login("a@example.com")
    await add_wishlist(c, "Visible via PAT")
    ro = await pat_client(c, ("read:papers",))
    assert len((await ro.get(f"{API}/papers/light")).json()) == 1
    r = await ro.post(f"{API}/papers/metadata-only", json={"title": "nope"})
    assert r.status_code == 403
    assert (await ro.patch(f"{API}/papers/{uuid.uuid4()}", json={"status": "read"})).status_code == 403
    rw = await pat_client(c, ("write:papers",))
    assert (await rw.get(f"{API}/papers/light")).status_code == 403
    assert (await rw.post(f"{API}/papers/metadata-only", json={"title": "ok"})).status_code == 201


# ---------------------------------------------------------------- /lookup
async def test_lookup(login, resolver):
    _, c = await login("a@example.com")
    r = await c.get(f"{API}/papers/lookup", params={"query": "arXiv:2405.12345"})
    assert r.status_code == 200
    body = r.json()
    assert body["kind"] == "arxiv" and body["results"][0]["identifier"] == "2405.12345"
    assert "@" in body["results"][0]["bibtex"]
    assert (await c.get(f"{API}/papers/light")).json() == []  # nothing stored

    resolver.error = UpstreamError("boom")
    r = await c.get(f"{API}/papers/lookup", params={"query": "2405.12345"})
    assert r.status_code == 502 and r.json() == {"detail": "upstream error"}
    resolver.error = ValueError("bad query")
    assert (await c.get(f"{API}/papers/lookup", params={"query": "2405.12345"})).status_code == 422
    assert (await c.get(f"{API}/papers/lookup", params={"query": ""})).status_code == 422
    assert (await c.get(f"{API}/papers/lookup")).status_code == 422


async def test_lookup_rate_limit(login, resolver):
    _, c = await login("a@example.com")
    codes = [(await c.get(f"{API}/papers/lookup", params={"query": "2405.12345"})).status_code for _ in range(31)]
    assert codes[:30] == [200] * 30 and codes[30] == 429


# ---------------------------------------------------------------- /metadata-only
async def test_metadata_only(login, resolver):
    user, c = await login("a@example.com")
    r = await c.post(f"{API}/papers/metadata-only", json={"identifier": "arXiv:2405.12345", "tags": ["x", "X", " y "]})
    assert r.status_code == 201, r.text
    p = r.json()
    assert p["identifier"] == "2405.12345" and p["identifier_type"] == "arxiv"
    assert p["status"] == "wishlist" and p["has_pdf"] is False and p["relative_file_path"] is None
    assert p["citekey"] == "smith2024scalable" and "@" in p["bibtex"] and "smith2024scalable" in p["bibtex"]
    assert sorted(p["tags"]) == ["x", "y"]
    assert p["workspace_id"] == await personal_ws(c)

    dup = await c.post(f"{API}/papers/metadata-only", json={"identifier": "2405.12345"})
    assert dup.status_code == 409
    assert (await c.post(f"{API}/papers/metadata-only", json={"identifier": "2999.99999"})).status_code == 404
    resolver.error = UpstreamError("x")
    assert (await c.post(f"{API}/papers/metadata-only", json={"identifier": "2405.12345"})).status_code == 502
    resolver.error = None

    # title path: manual metadata + internal identifier, citekeys disambiguated
    t1 = await add_wishlist(c, "Scalable Routing", authors=[{"family": "Smith"}], year=2024)
    t2 = await add_wishlist(c, "Scalable Routing", authors=[{"family": "Smith"}], year=2024)
    assert t1["identifier_type"] == "internal" and t1["identifier"].startswith("BF-")
    assert t1["citekey"] == "smith2024scalable" or t1["citekey"].startswith("smith2024scalable")
    assert t1["citekey"] != t2["citekey"] != p["citekey"]

    for bad in ({}, {"identifier": "2405.12345", "title": "t"}, {"title": "  "}, {"title": "t", "year": 0}):
        assert (await c.post(f"{API}/papers/metadata-only", json=bad)).status_code == 422, bad
    assert (await c.post(f"{API}/papers/metadata-only", json={"identifier": "just some title words"})).status_code == 422


# ---------------------------------------------------------------- GET / PATCH
async def test_get_and_patch(login, resolver):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    p = await add_wishlist(a, "Original title", authors=[{"family": "Doe", "given": "J"}], year=2020)
    q = await add_wishlist(a, "Another one", authors=[{"family": "Doe"}], year=2020)

    got = (await a.get(f"{API}/papers/{p['id']}")).json()
    assert set(got) >= LIGHT_KEYS | {"workspace_id", "abstract", "bibtex", "notes_md", "collection_ids", "index_status"}
    assert got["index_status"] == "pending" and got["notes_md"] == ""

    r = await a.patch(
        f"{API}/papers/{p['id']}",
        json={"title": "Brand New Title", "year": 2021, "status": "reading", "tags": ["a", "A", "b"],
              "notes_md": "# notes", "venue": "NSDI", "abstract": "abs"},
    )
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["title"] == "Brand New Title" and d["year"] == 2021 and d["status"] == "reading"
    assert sorted(d["tags"]) == ["a", "b"] and d["notes_md"] == "# notes" and d["venue"] == "NSDI"
    assert "Brand New Title" in d["bibtex"] and "2021" in d["bibtex"]
    assert d["relative_file_path"] is None

    # tags can be replaced / cleared; year nullable
    d = (await a.patch(f"{API}/papers/{p['id']}", json={"tags": [], "year": None})).json()
    assert d["tags"] == [] and d["year"] is None

    # citekey: change ok, conflict 409
    assert (await a.patch(f"{API}/papers/{p['id']}", json={"citekey": "mykey"})).json()["citekey"] == "mykey"
    assert "mykey" in (await a.get(f"{API}/papers/{p['id']}")).json()["bibtex"]
    r = await a.patch(f"{API}/papers/{q['id']}", json={"citekey": "mykey"})
    assert r.status_code == 409
    assert (await a.patch(f"{API}/papers/{p['id']}", json={"citekey": "mykey"})).status_code == 200  # own key

    # validation
    for bad in ({"title": None}, {"title": ""}, {"year": 0}, {"year": 10000}, {"status": "x"},
                {"citekey": "has space"}, {"tags": ["t"] * 31}, {"tags": ["x" * 101]}, {"authors": [{"family": ""}]}):
        assert (await a.patch(f"{API}/papers/{p['id']}", json=bad)).status_code == 422, bad

    # collection from another workspace -> 422; unknown -> 422
    async with get_sessionmaker()() as db:
        other_ws = (await db.execute(select(Workspace.id).where(Workspace.id == uuid.UUID(await personal_ws(b))))).scalar_one()
        col = Collection(workspace_id=other_ws, name="foreign")
        db.add(col)
        await db.commit()
        foreign = str(col.id)
    assert (await a.patch(f"{API}/papers/{p['id']}", json={"collection_ids": [foreign]})).status_code == 422
    assert (await a.patch(f"{API}/papers/{p['id']}", json={"collection_ids": [str(uuid.uuid4())]})).status_code == 422

    # IDOR
    assert (await b.get(f"{API}/papers/{p['id']}")).status_code == 404
    assert (await b.patch(f"{API}/papers/{p['id']}", json={"status": "read"})).status_code == 404
    assert (await b.get(f"{API}/papers/{p['id']}/file")).status_code == 404
    assert (await a.get(f"{API}/papers/{uuid.uuid4()}")).status_code == 404
    assert (await a.get(f"{API}/papers/not-a-uuid")).status_code == 422


async def test_routes_not_shadowed_by_paper_id(login, resolver):
    _, c = await login("a@example.com")
    assert (await c.get(f"{API}/papers/light")).status_code == 200
    assert (await c.get(f"{API}/papers/lookup", params={"query": "2405.12345"})).status_code == 200


# ---------------------------------------------------------------- file download
async def _upload(c, data, name="p.pdf", **form):
    return await c.post(f"{API}/papers/upload", files={"file": (name, data, "application/pdf")}, data=form)


async def test_file_download(login, resolver, storage, enqueued):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    data = make_pdf(["arXiv:2405.12345v1 [cs.NI]"])
    r = await _upload(a, data)
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    f = await a.get(f"{API}/papers/{pid}/file")
    assert f.status_code == 200 and f.content == data
    assert f.headers["content-type"] == "application/pdf"
    assert f.headers["content-disposition"].startswith("inline") and ".pdf" in f.headers["content-disposition"]
    assert f.headers["cache-control"] == "private, no-cache"
    import hashlib

    etag = f'"{hashlib.sha256(data).hexdigest()}"'
    assert f.headers["etag"] == etag
    # revalidation: 304 without a body, but access is still checked
    nm = await a.get(f"{API}/papers/{pid}/file", headers={"If-None-Match": etag})
    assert nm.status_code == 304 and nm.content == b"" and nm.headers["etag"] == etag
    assert (await a.get(f"{API}/papers/{pid}/file", headers={"If-None-Match": f'W/"x", {etag}'})).status_code == 304
    assert (await a.get(f"{API}/papers/{pid}/file", headers={"If-None-Match": '"other"'})).status_code == 200
    assert (await b.get(f"{API}/papers/{pid}/file", headers={"If-None-Match": etag})).status_code == 404
    ranged = await a.get(f"{API}/papers/{pid}/file", headers={"Range": "bytes=0-9"})
    assert ranged.status_code == 206 and ranged.content == data[:10] and ranged.headers["etag"] == etag
    assert (await b.get(f"{API}/papers/{pid}/file")).status_code == 404

    nofile = await add_wishlist(a, "No pdf yet")
    assert (await a.get(f"{API}/papers/{nofile['id']}/file")).status_code == 404

    pdf_files(storage.library)[0].unlink()
    assert (await a.get(f"{API}/papers/{pid}/file")).status_code == 404


async def test_file_download_traversal_path_is_404(login, resolver, storage):
    _, a = await login("a@example.com")
    p = await add_wishlist(a, "Evil")
    async with get_sessionmaker()() as db:
        await db.execute(update(Paper).where(Paper.id == uuid.UUID(p["id"])).values(has_pdf=True, relative_file_path="../../etc/passwd"))
        await db.commit()
    assert (await a.get(f"{API}/papers/{p['id']}/file")).status_code == 404


# ---------------------------------------------------------------- process_paper
async def test_process_paper(login, resolver, storage, enqueued):
    from app.workers.tasks import process_paper

    _, a = await login("a@example.com")
    r = await _upload(a, make_pdf(["arXiv:2405.12345v1 [cs.NI]"], pages=3))
    pid = r.json()["id"]
    assert enqueued.jobs == [("process_paper", pid)]
    async with get_sessionmaker()() as db:
        await db.execute(update(Paper).where(Paper.id == uuid.UUID(pid)).values(page_count=None))
        await db.commit()
    assert await process_paper({}, pid) == "ok"
    d = (await a.get(f"{API}/papers/{pid}")).json()
    assert d["page_count"] == 3 and d["index_status"] == "pending"

    # no pdf -> skipped
    w = await add_wishlist(a, "wish")
    assert await process_paper({}, w["id"]) == "skipped"
    assert await process_paper({}, str(uuid.uuid4())) == "skipped"

    # tampering -> failed
    path = pdf_files(storage.library)[0]
    path.write_bytes(path.read_bytes() + b"\n% tampered")
    assert await process_paper({}, pid) == "failed"
    assert (await a.get(f"{API}/papers/{pid}")).json()["index_status"] == "failed"

    # missing file -> failed
    path.unlink()
    assert await process_paper({}, pid) == "failed"
