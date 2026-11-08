import json
import uuid

from sqlalchemy import update

from app.db.session import get_sessionmaker
from app.models import Collection, Paper
from app.models.collection import paper_collections
from tests.helpers import join_workspace

API = "/api/v1"


async def personal_ws(c) -> str:
    return next(w["id"] for w in (await c.get(f"{API}/workspaces")).json() if w["kind"] == "personal")


async def add(c, title, **kw):
    r = await c.post(f"{API}/papers/metadata-only", json={"title": title, **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def test_export_formats_and_headers(login, resolver):
    _, c = await login("a@example.com")
    p = await add(c, "Clos & 100% fabrics", authors=[{"family": "Müller", "given": "Zoë"}], year=2023, tags=["net"])

    r = await c.get(f"{API}/export", params={"format": "bibtex"})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-bibtex")
    assert r.headers["content-disposition"] == 'attachment; filename="biblioflow-export.bib"'
    assert f"{{{p['citekey']}," in r.text and r"Clos \& 100\% fabrics" in r.text and "Müller, Zoë" in r.text

    r = await c.get(f"{API}/export", params={"format": "ris"})
    assert r.headers["content-type"].startswith("application/x-research-info-systems")
    assert "TI  - Clos & 100% fabrics" in r.text and "ER  - " in r.text

    r = await c.get(f"{API}/export", params={"format": "csljson"})
    assert r.headers["content-type"].startswith("application/vnd.citationstyles.csl+json")
    item = json.loads(r.text)[0]
    assert item["id"] == p["citekey"] and item["type"] and item["title"] == "Clos & 100% fabrics"
    assert item["author"] == [{"family": "Müller", "given": "Zoë"}] and item["issued"]["date-parts"] == [[2023]]

    assert (await c.get(f"{API}/export", params={"format": "docx"})).status_code == 422


async def test_export_filters(login, resolver):
    _, c = await login("a@example.com")
    p1 = await add(c, "Alpha paper", year=2023, tags=["bgp"])
    p2 = await add(c, "Beta paper", year=2024, tags=["other"])
    async with get_sessionmaker()() as db:
        col = Collection(workspace_id=uuid.UUID(p1["workspace_id"]), name="Reading")
        db.add(col)
        await db.flush()
        await db.execute(paper_collections.insert().values(paper_id=uuid.UUID(p1["id"]), collection_id=col.id))
        await db.commit()
        cid = str(col.id)

    async def keys(**params):
        r = await c.get(f"{API}/export", params={"format": "csljson", **params})
        assert r.status_code == 200, r.text
        return {i["id"] for i in json.loads(r.text)}

    both = {p1["citekey"], p2["citekey"]}
    assert await keys() == both
    assert await keys(tag="bgp") == {p1["citekey"]}
    assert await keys(year=2024) == {p2["citekey"]}
    assert await keys(q="beta") == {p2["citekey"]}
    assert await keys(collection_id=cid) == {p1["citekey"]}
    assert await keys(status="read") == set()
    assert await keys(workspace_id=await personal_ws(c)) == both


async def test_export_citekey_unique_across_workspaces(login, resolver):
    _, c = await login("a@example.com")
    p1 = await add(c, "Same title", year=2024)
    r = await c.post(f"{API}/workspaces", json={"name": "Lab"})
    assert r.status_code in (200, 201), r.text
    ws2 = r.json()["id"]
    r = await c.post(f"{API}/papers/metadata-only", json={"workspace_id": ws2, "title": "Same title", "year": 2024})
    assert r.status_code == 201, r.text
    p2 = r.json()
    async with get_sessionmaker()() as db:
        await db.execute(update(Paper).where(Paper.id == uuid.UUID(p2["id"])).values(citekey=p1["citekey"]))
        await db.commit()
    ids = [i["id"] for i in json.loads((await c.get(f"{API}/export", params={"format": "csljson"})).text)]
    assert len(ids) == 2 and len(set(ids)) == 2 and p1["citekey"] in ids


async def test_export_authorization(login, pat_client, resolver):
    _, owner = await login("a@example.com")
    await add(owner, "Private paper")
    ws = await personal_ws(owner)

    _, other = await login("b@example.com")
    assert (await other.get(f"{API}/export", params={"workspace_id": ws})).status_code == 404
    r = await other.get(f"{API}/export", params={"format": "csljson"})
    assert r.status_code == 200 and json.loads(r.text) == []

    ok = await pat_client(owner, scopes=("read:papers",))
    r = await ok.get(f"{API}/export", params={"format": "bibtex"})
    assert r.status_code == 200 and "Private paper" in r.text

    no_scope = await pat_client(owner, scopes=("write:papers",))
    assert (await no_scope.get(f"{API}/export")).status_code == 403

    anon = await pat_client(owner)
    anon.headers.pop("Authorization")
    assert (await anon.get(f"{API}/export")).status_code == 401


async def test_export_shared_workspace_member_can_export(login, resolver):
    _, owner = await login("a@example.com")
    r = await owner.post(f"{API}/workspaces", json={"name": "Lab"})
    ws = r.json()["id"]
    await owner.post(f"{API}/papers/metadata-only", json={"workspace_id": ws, "title": "Shared one"})
    _, member = await login("b@example.com")
    await join_workspace(owner, member, ws, "b@example.com")
    r = await member.get(f"{API}/export", params={"workspace_id": ws, "format": "ris"})
    assert r.status_code == 200 and "Shared one" in r.text
