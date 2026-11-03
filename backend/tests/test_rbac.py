import uuid

import pytest
from fastapi import HTTPException

from app.api.deps import Principal, get_paper_for_user, get_workspace_for_user
from app.core.security import ALL_SCOPES
from app.db.session import get_sessionmaker
from app.models import IdentifierType, Paper, User, WorkspaceRole
from tests.helpers import join_workspace

API = "/api/v1"


async def _personal_ws_id(c):
    return next(w["id"] for w in (await c.get(f"{API}/workspaces")).json() if w["kind"] == "personal")


async def test_list_shows_only_own_workspaces(login):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    la, lb = (await a.get(f"{API}/workspaces")).json(), (await b.get(f"{API}/workspaces")).json()
    assert len(la) == len(lb) == 1
    assert la[0]["id"] != lb[0]["id"]
    assert la[0]["role"] == "owner" and la[0]["kind"] == "personal"


async def test_idor_personal_workspace_404(login):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    ws = await _personal_ws_id(a)
    assert (await a.get(f"{API}/workspaces/{ws}")).status_code == 200
    r = await b.get(f"{API}/workspaces/{ws}")
    missing = await b.get(f"{API}/workspaces/{uuid.uuid4()}")
    assert r.status_code == missing.status_code == 404
    assert r.json() == missing.json()
    r = await b.post(f"{API}/workspaces/{ws}/members", json={"email": "b@example.com", "role": "owner"})
    assert r.status_code == 404


async def test_shared_workspace_roles(login):
    owner, a = await login("a@example.com")
    editor, e = await login("e@example.com")
    viewer, v = await login("v@example.com")
    outsider, o = await login("o@example.com")

    ws = (await a.post(f"{API}/workspaces", json={"name": "Lab Network"})).json()
    assert ws["kind"] == "shared" and ws["role"] == "owner" and ws["slug"].startswith("lab-network-")
    wid = ws["id"]

    await join_workspace(a, e, wid, "e@example.com", "editor")
    await join_workspace(a, v, wid, "v@example.com", "viewer")
    dup = await a.post(f"{API}/workspaces/{wid}/members", json={"email": "v@example.com", "role": "viewer"})
    assert dup.status_code == 409
    # unknown e-mail is indistinguishable from a known one
    assert (await a.post(f"{API}/workspaces/{wid}/members", json={"email": "nobody@example.com", "role": "viewer"})).status_code == 202

    # members can read; roles are reported correctly
    d = (await v.get(f"{API}/workspaces/{wid}")).json()
    assert d["role"] == "viewer" and {m["email"]: m["role"] for m in d["members"]} == {
        "a@example.com": "owner", "e@example.com": "editor", "v@example.com": "viewer"}
    assert (await e.get(f"{API}/workspaces/{wid}")).json()["role"] == "editor"
    assert len((await v.get(f"{API}/workspaces")).json()) == 2

    # only owner may add members (others get 404, not 403)
    for c in (e, v, o):
        r = await c.post(f"{API}/workspaces/{wid}/members", json={"email": "o@example.com", "role": "viewer"})
        assert r.status_code == 404
    # outsider sees nothing
    assert (await o.get(f"{API}/workspaces/{wid}")).status_code == 404
    assert len((await o.get(f"{API}/workspaces")).json()) == 1


async def test_cannot_add_members_to_personal_workspace(login):
    _, a = await login("a@example.com")
    await login("b@example.com")
    ws = await _personal_ws_id(a)
    r = await a.post(f"{API}/workspaces/{ws}/members", json={"email": "b@example.com", "role": "viewer"})
    assert r.status_code == 400


async def test_get_workspace_and_paper_helpers(login):
    owner, a = await login("a@example.com")
    viewer, v = await login("v@example.com")
    outsider, _ = await login("o@example.com")
    ws = (await a.post(f"{API}/workspaces", json={"name": "Lab"})).json()
    await join_workspace(a, v, ws["id"], "v@example.com", "viewer")
    wid = uuid.UUID(ws["id"])

    async with get_sessionmaker()() as db:
        paper = Paper(workspace_id=wid, created_by=owner.id, identifier="2405.12345",
                      identifier_type=IdentifierType.arxiv, title="Scalable BGP")
        db.add(paper)
        await db.commit()
        pid = paper.id

        async def principal(u):
            return Principal(user=await db.get(User, u.id), auth_method="session", scopes=frozenset(ALL_SCOPES))

        po, pv, px = await principal(owner), await principal(viewer), await principal(outsider)
        assert (await get_paper_for_user(db, pid, po, WorkspaceRole.owner)).id == pid
        assert (await get_paper_for_user(db, pid, pv)).id == pid
        for p, role in ((pv, WorkspaceRole.editor), (pv, WorkspaceRole.owner), (px, WorkspaceRole.viewer)):
            with pytest.raises(HTTPException) as ei:
                await get_paper_for_user(db, pid, p, role)
            assert ei.value.status_code == 404
        with pytest.raises(HTTPException) as ei:
            await get_paper_for_user(db, uuid.uuid4(), po)
        assert ei.value.status_code == 404

        ws_obj, role = await get_workspace_for_user(db, wid, pv)
        assert role == WorkspaceRole.viewer
        with pytest.raises(HTTPException) as ei:
            await get_workspace_for_user(db, wid, px)
        assert ei.value.status_code == 404


async def test_generated_columns_and_unique_identifier(login):
    owner, a = await login("a@example.com")
    ws = uuid.UUID(await _personal_ws_id(a))
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    async with get_sessionmaker()() as db:
        db.add(Paper(workspace_id=ws, identifier="BF-ABC", identifier_type=IdentifierType.internal,
                     title="Clos Fabrics", abstract="routing", venue="SIGCOMM"))
        await db.commit()
        hit = (await db.execute(text("SELECT count(*) FROM papers WHERE search_tsv @@ plainto_tsquery('simple','routing')"))).scalar()
        assert hit == 1
        db.add(Paper(workspace_id=ws, identifier="BF-ABC", identifier_type=IdentifierType.internal, title="dup"))
        with pytest.raises(IntegrityError):
            await db.commit()
