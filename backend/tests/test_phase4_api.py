import asyncio

from sqlalchemy import select, update

from app.db.session import get_sessionmaker
from app.models import Annotation, Paper
from tests.helpers import join_workspace, pdf_files
from tests.pdfgen import make_pdf

API = "/api/v1"
OLD, NEW = "correct-horse-battery", "another-long-password"


async def personal_ws(c) -> str:
    return next(w["id"] for w in (await c.get(f"{API}/workspaces")).json() if w["kind"] == "personal")


async def add_wishlist(c, title, **kw):
    r = await c.post(f"{API}/papers/metadata-only", json={"title": title, **kw})
    assert r.status_code == 201, r.text
    return r.json()


async def shared_ws(owner, **members):
    """Shared workspace owned by ``owner``; ``members`` maps email -> (client, role); each invite is accepted."""
    ws = (await owner.post(f"{API}/workspaces", json={"name": "Lab"})).json()["id"]
    for email, (client, role) in members.items():
        await join_workspace(owner, client, ws, email, role)
    return ws


# ---------------------------------------------------------------- password
async def test_password_change(login, make_client, pat_client):
    _, c1 = await login("a@example.com", OLD)
    c2 = make_client()
    assert (await c2.post(f"{API}/auth/login", json={"email": "a@example.com", "password": OLD})).status_code == 200
    await asyncio.sleep(1.1)  # iat has 1s resolution

    pat = await pat_client(c1)
    assert (await pat.post(f"{API}/auth/password", json={"current_password": OLD, "new_password": NEW})).status_code == 403
    assert (await c1.post(f"{API}/auth/password", json={"current_password": "nope-nope-nope", "new_password": NEW})).status_code == 400
    assert (await c1.post(f"{API}/auth/password", json={"current_password": OLD, "new_password": "short"})).status_code == 422

    r = await c1.post(f"{API}/auth/password", json={"current_password": OLD, "new_password": NEW})
    assert r.status_code == 204
    assert "bf_session=" in r.headers["set-cookie"]
    assert (await c1.get(f"{API}/auth/me")).status_code == 200  # re-issued cookie
    assert (await c2.get(f"{API}/auth/me")).status_code == 401  # other session rejected
    assert (await pat.get(f"{API}/auth/me")).status_code == 200  # PATs unaffected

    c3 = make_client()
    body = {"email": "a@example.com", "password": OLD}
    assert (await c3.post(f"{API}/auth/login", json=body)).status_code == 401
    assert (await c3.post(f"{API}/auth/login", json={**body, "password": NEW})).status_code == 200
    assert (await c3.get(f"{API}/auth/me")).status_code == 200


# ---------------------------------------------------------------- paper delete
async def test_delete_paper_removes_file_and_children(login, resolver, storage, enqueued):
    _, c = await login("a@example.com")
    r = await c.post(f"{API}/papers/upload", files={"file": ("p.pdf", make_pdf(["hello"]), "application/pdf")})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    assert (await c.patch(f"{API}/papers/{pid}", json={"tags": ["x"]})).status_code == 200
    ann = await c.post(f"{API}/papers/{pid}/annotations", json={"kind": "highlight", "page_number": 1, "position": {"a": 1}})
    assert ann.status_code == 201
    assert len(pdf_files(storage.library)) == 1

    assert (await c.delete(f"{API}/papers/{pid}")).status_code == 204
    assert pdf_files(storage.library) == []
    assert (await c.get(f"{API}/papers/{pid}")).status_code == 404
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(Annotation))).first() is None


async def test_delete_paper_keeps_shared_file_and_checks_rbac(login, resolver, storage, enqueued):
    _, a = await login("a@example.com")
    _, v = await login("v@example.com")
    _, o = await login("o@example.com")
    r = await a.post(f"{API}/papers/upload", files={"file": ("p.pdf", make_pdf(["hello"]), "application/pdf")})
    pid = r.json()["id"]
    # a second paper row (as in another workspace) pointing at the same stored file
    async with get_sessionmaker()() as db:
        src = await db.get(Paper, pid)
        ws2 = await shared_ws(a)
        db.add(Paper(workspace_id=ws2, identifier="other", identifier_type=src.identifier_type, title="dup",
                     has_pdf=True, relative_file_path=src.relative_file_path))
        await db.commit()
    assert (await o.delete(f"{API}/papers/{pid}")).status_code == 404
    assert (await a.delete(f"{API}/papers/{pid}")).status_code == 204
    assert len(pdf_files(storage.library)) == 1

    ws = await shared_ws(a, **{"v@example.com": (v, "viewer")})
    p = await add_wishlist(a, "In shared", workspace_id=ws)
    assert (await v.delete(f"{API}/papers/{p['id']}")).status_code == 404


# ---------------------------------------------------------------- tags / collections
async def test_tags_and_collections(login, resolver):
    _, a = await login("a@example.com")
    _, o = await login("o@example.com")
    ws = await personal_ws(a)
    p1 = await add_wishlist(a, "One", tags=["zeta", "Alpha"])
    await add_wishlist(a, "Two", tags=["zeta"])
    tags = (await a.get(f"{API}/tags", params={"workspace_id": ws})).json()
    assert [(t["name"], t["paper_count"]) for t in tags] == [("Alpha", 1), ("zeta", 2)]
    assert set(tags[0]) == {"id", "name", "color", "paper_count"}
    assert (await o.get(f"{API}/tags", params={"workspace_id": ws})).status_code == 404
    assert (await a.get(f"{API}/tags")).status_code == 422

    root = (await a.post(f"{API}/collections", json={"workspace_id": ws, "name": "Root"})).json()
    child = await a.post(f"{API}/collections", json={"workspace_id": ws, "name": "Child", "parent_id": root["id"]})
    assert child.status_code == 201
    child = child.json()
    grand = (await a.post(f"{API}/collections", json={"workspace_id": ws, "name": "G", "parent_id": child["id"]})).json()
    assert (await a.patch(f"{API}/papers/{p1['id']}", json={"collection_ids": [child["id"]]})).status_code == 200

    cols = {c["id"]: c for c in (await a.get(f"{API}/collections", params={"workspace_id": ws})).json()}
    assert cols[child["id"]]["paper_count"] == 1 and cols[root["id"]]["paper_count"] == 0
    assert cols[child["id"]]["parent_id"] == root["id"]

    assert (await a.patch(f"{API}/collections/{root['id']}", json={"parent_id": grand["id"]})).status_code == 422
    assert (await a.patch(f"{API}/collections/{root['id']}", json={"parent_id": root["id"]})).status_code == 422
    r = await a.patch(f"{API}/collections/{child['id']}", json={"name": "Renamed"})
    assert r.status_code == 200 and r.json()["name"] == "Renamed" and r.json()["paper_count"] == 1
    # parent in another workspace -> 404
    ws_b = await personal_ws(o)
    other = (await o.post(f"{API}/collections", json={"workspace_id": ws_b, "name": "X"})).json()
    assert (await a.patch(f"{API}/collections/{root['id']}", json={"parent_id": other["id"]})).status_code == 404
    assert (await o.patch(f"{API}/collections/{root['id']}", json={"name": "hack"})).status_code == 404
    assert (await a.post(f"{API}/collections", json={"workspace_id": ws_b, "name": "n"})).status_code == 404

    assert (await a.delete(f"{API}/collections/{root['id']}")).status_code == 204
    assert (await a.get(f"{API}/collections", params={"workspace_id": ws})).json() == []
    assert (await a.get(f"{API}/papers/{p1['id']}")).status_code == 200  # paper kept


# ---------------------------------------------------------------- annotations
async def test_annotations(login, resolver):
    _, owner = await login("a@example.com")
    _, ed1 = await login("e1@example.com")
    _, ed2 = await login("e2@example.com")
    _, viewer = await login("v@example.com")
    _, outsider = await login("o@example.com")
    ws = await shared_ws(owner, **{"e1@example.com": (ed1, "editor"), "e2@example.com": (ed2, "editor"), "v@example.com": (viewer, "viewer")})
    pid = (await add_wishlist(owner, "Shared", workspace_id=ws))["id"]
    url = f"{API}/papers/{pid}/annotations"
    body = {"kind": "highlight", "page_number": 2, "position": {"rects": [[0, 0, 1, 1]]}, "content_text": "t", "color": "#aabbcc"}

    r = await ed1.post(url, json=body)
    assert r.status_code == 201, r.text
    ann = r.json()
    assert ann["user_display_name"] == "e1" and ann["color"] == "#aabbcc" and ann["paper_id"] == pid
    first = (await ed1.post(url, json={**body, "page_number": 1})).json()
    assert [a["id"] for a in (await viewer.get(url)).json()] == [first["id"], ann["id"]]  # page order

    assert (await viewer.post(url, json=body)).status_code == 404
    assert (await outsider.post(url, json=body)).status_code == 404
    assert (await outsider.get(url)).status_code == 404
    assert (await ed1.post(url, json={**body, "page_number": 0})).status_code == 422
    assert (await ed1.post(url, json={**body, "color": "red"})).status_code == 422
    big = {**body, "position": {"blob": "x" * 70000}}
    assert (await ed1.post(url, json=big)).status_code == 422

    aurl = f"{API}/annotations/{ann['id']}"
    assert (await ed2.patch(aurl, json={"comment": "x"})).status_code == 403
    assert (await ed2.delete(aurl)).status_code == 403
    assert (await viewer.delete(aurl)).status_code == 403
    assert (await outsider.patch(aurl, json={"comment": "x"})).status_code == 404
    assert (await outsider.delete(aurl)).status_code == 404
    r = await ed1.patch(aurl, json={"comment": "mine", "color": "#112233"})
    assert r.status_code == 200 and r.json()["comment"] == "mine" and r.json()["color"] == "#112233"
    assert (await owner.patch(aurl, json={"comment": "by owner"})).status_code == 200
    assert (await ed1.delete(aurl)).status_code == 204
    assert (await owner.delete(f"{API}/annotations/{first['id']}")).status_code == 204
    assert (await owner.get(url)).json() == []
