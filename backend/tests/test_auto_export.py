import json
import uuid

from sqlalchemy import select, update

from app.db.session import get_sessionmaker
from app.models import AutoExport, Collection, Paper
from app.models.collection import paper_collections
from app.services.auto_export import sync_auto_exports
from tests.helpers import join_workspace

API = "/api/v1"


async def personal_ws(c) -> dict:
    return next(w for w in (await c.get(f"{API}/workspaces")).json() if w["kind"] == "personal")


async def add(c, title, **kw):
    r = await c.post(f"{API}/papers/metadata-only", json={"title": title, **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def make(c, ws_id, **body):
    body = {"format": "bibtex", "filename": "refs.bib", **body}
    return await c.post(f"{API}/workspaces/{ws_id}/auto-exports", json=body)


async def sync(storage):
    return await sync_auto_exports(get_sessionmaker(), storage.exports)


async def test_crud_and_permissions(login, storage, resolver):
    _, owner = await login("a@example.com")
    ws = await personal_ws(owner)
    r = await make(owner, ws["id"], tag="bgp", year=2024)
    assert r.status_code == 201, r.text
    ae = r.json()
    assert ae["path"] == f"{ws['slug']}/refs.bib" and ae["tag"] == "bgp" and ae["last_written_at"] is None

    assert (await make(owner, ws["id"])).status_code == 409  # same filename
    listed = (await owner.get(f"{API}/workspaces/{ws['id']}/auto-exports")).json()
    assert [a["id"] for a in listed] == [ae["id"]]

    # another user sees the workspace as non-existent
    _, other = await login("b@example.com")
    assert (await other.get(f"{API}/workspaces/{ws['id']}/auto-exports")).status_code == 404
    assert (await make(other, ws["id"], filename="x.bib")).status_code == 404
    assert (await other.delete(f"{API}/workspaces/{ws['id']}/auto-exports/{ae['id']}")).status_code == 404

    # a viewer can list but not create / delete
    shared = (await owner.post(f"{API}/workspaces", json={"name": "Lab"})).json()
    await join_workspace(owner, other, shared["id"], "b@example.com", role="viewer")
    assert (await other.get(f"{API}/workspaces/{shared['id']}/auto-exports")).status_code == 200
    assert (await make(other, shared["id"])).status_code == 404
    mine = (await make(owner, shared["id"])).json()
    assert (await other.delete(f"{API}/workspaces/{shared['id']}/auto-exports/{mine['id']}")).status_code == 404
    # an id from another workspace is a 404 even for an editor of both
    assert (await owner.delete(f"{API}/workspaces/{shared['id']}/auto-exports/{ae['id']}")).status_code == 404

    assert (await owner.delete(f"{API}/workspaces/{ws['id']}/auto-exports/{ae['id']}")).status_code == 204
    assert (await owner.get(f"{API}/workspaces/{ws['id']}/auto-exports")).json() == []


async def test_pat_scopes(login, pat_client, storage, resolver):
    _, owner = await login("a@example.com")
    ws = await personal_ws(owner)
    reader = await pat_client(owner, scopes=("read:papers",))
    assert (await reader.get(f"{API}/workspaces/{ws['id']}/auto-exports")).status_code == 200
    assert (await make(reader, ws["id"])).status_code == 403
    writer = await pat_client(owner)
    assert (await make(writer, ws["id"])).status_code == 201


async def test_filename_and_collection_validation(login, storage, resolver):
    _, owner = await login("a@example.com")
    ws = await personal_ws(owner)
    for name in ["../x.bib", "a/b.bib", ".h.bib", "x.ris", "x", "a b.bib", "x.bib\x00"]:
        assert (await make(owner, ws["id"], filename=name)).status_code == 422, name
    assert (await make(owner, ws["id"], format="docx")).status_code == 422
    assert (await make(owner, ws["id"], format="ris", filename="x.ris")).status_code == 201
    # a collection of another workspace is not accepted
    other_ws = (await owner.post(f"{API}/workspaces", json={"name": "Lab"})).json()
    async with get_sessionmaker()() as db:
        col = Collection(workspace_id=uuid.UUID(other_ws["id"]), name="C")
        db.add(col)
        await db.commit()
        cid = str(col.id)
    assert (await make(owner, ws["id"], filename="c.bib", collection_id=cid)).status_code == 404
    assert (await make(owner, other_ws["id"], filename="c.bib", collection_id=cid)).status_code == 201


async def test_writer_matches_export_and_detects_changes(login, storage, resolver):
    _, c = await login("a@example.com")
    ws = await personal_ws(c)
    p1 = await add(c, "Alpha paper", year=2023, tags=["bgp"])
    p2 = await add(c, "Beta paper", year=2024, tags=["bgp"])
    await add(c, "Gamma paper", year=2024, tags=["other"])
    assert (await make(c, ws["id"], tag="bgp")).status_code == 201
    assert (await make(c, ws["id"], format="csljson", filename="all.json")).status_code == 201

    res = await sync(storage)
    assert len(res.written) == 2 and not res.failed
    path = storage.exports / ws["slug"] / "refs.bib"
    expected = (await c.get(f"{API}/export", params={"format": "bibtex", "tag": "bgp", "workspace_id": ws["id"]})).text
    assert path.read_text(encoding="utf-8") == expected
    assert "Alpha paper" in expected and "Gamma paper" not in expected
    all_json = json.loads((storage.exports / ws["slug"] / "all.json").read_text(encoding="utf-8"))
    assert len(all_json) == 3
    listed = (await c.get(f"{API}/workspaces/{ws['id']}/auto-exports")).json()
    assert all(a["last_written_at"] and a["last_error"] is None for a in listed)
    assert {a["filename"]: a["paper_count"] for a in listed} == {"refs.bib": 2, "all.json": 3}
    assert not [f for f in path.parent.iterdir() if f.name.endswith(".tmp")]

    # nothing changed: not rewritten
    inode = path.stat().st_ino
    res = await sync(storage)
    assert len(res.written) == 0 and len(res.skipped) == 2
    assert path.stat().st_ino == inode

    # title edit
    r = await c.patch(f"{API}/papers/{p1['id']}", json={"title": "Alpha paper revised"})
    assert r.status_code == 200, r.text
    res = await sync(storage)
    assert len(res.written) == 2
    assert "Alpha paper revised" in path.read_text(encoding="utf-8")

    # delete a paper
    assert (await c.delete(f"{API}/papers/{p2['id']}")).status_code == 204
    res = await sync(storage)
    assert len(res.written) == 2
    assert "Beta paper" not in path.read_text(encoding="utf-8")
    assert len(json.loads((storage.exports / ws["slug"] / "all.json").read_text(encoding="utf-8"))) == 2

    # a deleted file is restored
    path.unlink()
    res = await sync(storage)
    assert path.is_file() and len(res.written) == 1


async def test_collection_membership_change_rewrites(login, storage, resolver):
    _, c = await login("a@example.com")
    ws = await personal_ws(c)
    p1 = await add(c, "Alpha paper")
    async with get_sessionmaker()() as db:
        col = Collection(workspace_id=uuid.UUID(ws["id"]), name="Reading")
        db.add(col)
        await db.commit()
        cid = col.id
    assert (await make(c, ws["id"], collection_id=str(cid))).status_code == 201
    await sync(storage)
    path = storage.exports / ws["slug"] / "refs.bib"
    assert "Alpha paper" not in path.read_text(encoding="utf-8")
    async with get_sessionmaker()() as db:  # membership only: the paper row itself is untouched
        await db.execute(paper_collections.insert().values(paper_id=uuid.UUID(p1["id"]), collection_id=cid))
        await db.commit()
    assert len((await sync(storage)).written) == 1
    assert "Alpha paper" in path.read_text(encoding="utf-8")


async def test_failure_is_recorded_and_does_not_stop_others(login, storage, resolver, monkeypatch):
    _, c = await login("a@example.com")
    ws = await personal_ws(c)
    await add(c, "Alpha paper")
    bad = (await make(c, ws["id"], filename="bad.bib")).json()
    assert (await make(c, ws["id"], filename="good.bib")).status_code == 201

    from app.services import auto_export

    real = auto_export.write_atomic

    def flaky(target, data):
        if target.name == "bad.bib":
            raise OSError("read-only file system")
        real(target, data)

    monkeypatch.setattr(auto_export, "write_atomic", flaky)
    res = await sync(storage)
    assert len(res.failed) == 1 and len(res.written) == 1
    assert (storage.exports / ws["slug"] / "good.bib").is_file()
    async with get_sessionmaker()() as db:
        row = (await db.execute(select(AutoExport).where(AutoExport.id == uuid.UUID(bad["id"])))).scalar_one()
        assert row.last_error and "OSError" in row.last_error and row.last_written_at is None

    # recovers on the next pass (a recorded error forces a retry even without paper changes)
    monkeypatch.setattr(auto_export, "write_atomic", real)
    res = await sync(storage)
    assert len(res.written) == 1 and not res.failed
    async with get_sessionmaker()() as db:
        row = (await db.execute(select(AutoExport).where(AutoExport.id == uuid.UUID(bad["id"])))).scalar_one()
        assert row.last_error is None and row.last_written_at is not None


async def test_delete_removes_file(login, storage, resolver):
    _, c = await login("a@example.com")
    ws = await personal_ws(c)
    await add(c, "Alpha paper")
    ae = (await make(c, ws["id"])).json()
    await sync(storage)
    path = storage.exports / ws["slug"] / "refs.bib"
    assert path.is_file()
    assert (await c.delete(f"{API}/workspaces/{ws['id']}/auto-exports/{ae['id']}")).status_code == 204
    assert not path.exists()
    assert len((await sync(storage)).written) == 0
