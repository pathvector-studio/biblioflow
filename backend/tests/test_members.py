import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import update

from app.db.session import get_sessionmaker
from app.models import WorkspaceInvitation
from tests.helpers import join_workspace

API = "/api/v1"


async def _invite(owner, ws, email, role="viewer"):
    return await owner.post(f"{API}/workspaces/{ws}/members", json={"email": email, "role": role})


async def _shared(owner):
    return (await owner.post(f"{API}/workspaces", json={"name": "Lab"})).json()["id"]


async def _my_invites(c):
    return (await c.get(f"{API}/invitations")).json()


async def test_invite_response_does_not_reveal_account(login, create_user):
    _, a = await login("a@example.com")
    inactive = await create_user("gone@example.com")
    async with get_sessionmaker()() as db:
        from app.models import User

        await db.execute(update(User).where(User.id == inactive.id).values(is_active=False))
        await db.commit()
    await create_user("exists@example.com")
    ws = await _shared(a)
    rs = [await _invite(a, ws, e) for e in ("exists@example.com", "nobody@example.com", "gone@example.com")]
    assert {r.status_code for r in rs} == {202}
    bodies = [r.json() for r in rs]
    assert {tuple(sorted(b)) for b in bodies} == {("email", "role", "status")}
    assert {b["status"] for b in bodies} == {"pending"}


async def test_invite_before_account_exists_then_accept(login, create_user, make_client):
    _, a = await login("a@example.com")
    ws = await _shared(a)
    assert (await _invite(a, ws, "Late@Example.com", "editor")).status_code == 202
    _, late = await login("late@example.com")
    invs = await _my_invites(late)
    assert len(invs) == 1 and invs[0]["workspace_name"] == "Lab" and invs[0]["role"] == "editor"
    assert invs[0]["invited_by_name"]
    r = await late.post(f"{API}/invitations/{invs[0]['id']}/accept")
    assert r.status_code == 200 and r.json()["role"] == "editor" and r.json()["id"] == ws
    assert await _my_invites(late) == []
    assert (await late.get(f"{API}/workspaces/{ws}")).status_code == 200


async def test_reinvite_overrides_role_and_member_conflict(login):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    ws = await _shared(a)
    await _invite(a, ws, "b@example.com", "viewer")
    assert (await _invite(a, ws, "b@example.com", "editor")).status_code == 202
    pending = (await a.get(f"{API}/workspaces/{ws}/invitations")).json()
    assert len(pending) == 1 and pending[0]["role"] == "editor"
    invs = await _my_invites(b)
    assert len(invs) == 1
    assert (await b.post(f"{API}/invitations/{invs[0]['id']}/accept")).json()["role"] == "editor"
    assert (await _invite(a, ws, "b@example.com")).status_code == 409
    assert (await a.get(f"{API}/workspaces/{ws}/invitations")).json() == []


async def test_other_users_invitation_is_404(login):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    _, c = await login("c@example.com")
    ws = await _shared(a)
    await _invite(a, ws, "b@example.com")
    inv = (await _my_invites(b))[0]["id"]
    assert await _my_invites(c) == []
    assert (await c.post(f"{API}/invitations/{inv}/accept")).status_code == 404
    assert (await c.post(f"{API}/invitations/{inv}/decline")).status_code == 404
    assert (await c.post(f"{API}/invitations/{uuid.uuid4()}/accept")).status_code == 404
    assert (await c.get(f"{API}/workspaces/{ws}")).status_code == 404
    assert (await b.post(f"{API}/invitations/{inv}/decline")).status_code == 204
    assert await _my_invites(b) == []
    assert (await b.get(f"{API}/workspaces/{ws}")).status_code == 404


async def test_expired_invitation(login):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    ws = await _shared(a)
    await _invite(a, ws, "b@example.com")
    inv = (await _my_invites(b))[0]["id"]
    async with get_sessionmaker()() as db:
        await db.execute(update(WorkspaceInvitation).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await db.commit()
    assert await _my_invites(b) == []
    assert (await a.get(f"{API}/workspaces/{ws}/invitations")).json() == []
    assert (await b.post(f"{API}/invitations/{inv}/accept")).status_code == 404
    assert (await b.get(f"{API}/workspaces/{ws}")).status_code == 404
    # re-inviting refreshes the expired row
    assert (await _invite(a, ws, "b@example.com")).status_code == 202
    assert len(await _my_invites(b)) == 1


async def test_owner_revokes_invitation(login):
    _, a = await login("a@example.com")
    _, b = await login("b@example.com")
    ws = await _shared(a)
    await _invite(a, ws, "b@example.com")
    inv = (await a.get(f"{API}/workspaces/{ws}/invitations")).json()[0]["id"]
    assert (await b.get(f"{API}/workspaces/{ws}/invitations")).status_code == 404
    assert (await b.delete(f"{API}/workspaces/{ws}/invitations/{inv}")).status_code == 404
    assert (await a.delete(f"{API}/workspaces/{ws}/invitations/{inv}")).status_code == 204
    assert (await a.delete(f"{API}/workspaces/{ws}/invitations/{inv}")).status_code == 404
    assert await _my_invites(b) == []


async def test_role_change_and_removal(login):
    _, a = await login("a@example.com")
    ev, e = await login("e@example.com")
    vu, v = await login("v@example.com")
    _, o = await login("o@example.com")
    ws = await _shared(a)
    await join_workspace(a, e, ws, "e@example.com", "editor")
    await join_workspace(a, v, ws, "v@example.com", "viewer")
    p = (await a.post(f"{API}/papers/metadata-only", json={"title": "Shared paper", "workspace_id": ws})).json()

    # only owners may change roles / remove others (others see 404)
    for c in (e, v, o):
        assert (await c.patch(f"{API}/workspaces/{ws}/members/{vu.id}", json={"role": "owner"})).status_code == 404
    assert (await e.delete(f"{API}/workspaces/{ws}/members/{vu.id}")).status_code == 404

    r = await a.patch(f"{API}/workspaces/{ws}/members/{vu.id}", json={"role": "editor"})
    assert r.status_code == 200 and r.json()["role"] == "editor"
    assert (await v.get(f"{API}/workspaces/{ws}")).json()["role"] == "editor"
    assert (await a.patch(f"{API}/workspaces/{ws}/members/{uuid.uuid4()}", json={"role": "editor"})).status_code == 404

    assert (await v.get(f"{API}/papers/{p['id']}")).status_code == 200
    assert (await a.delete(f"{API}/workspaces/{ws}/members/{vu.id}")).status_code == 204
    assert (await v.get(f"{API}/papers/{p['id']}")).status_code == 404
    assert (await v.get(f"{API}/workspaces/{ws}")).status_code == 404
    assert (await v.get(f"{API}/papers/light", params={"workspace_id": ws})).status_code == 404
    assert ws not in [w["id"] for w in (await v.get(f"{API}/workspaces")).json()]
    assert (await a.delete(f"{API}/workspaces/{ws}/members/{vu.id}")).status_code == 404


async def test_member_can_leave(login):
    _, a = await login("a@example.com")
    eu, e = await login("e@example.com")
    ws = await _shared(a)
    await join_workspace(a, e, ws, "e@example.com", "editor")
    assert (await e.delete(f"{API}/workspaces/{ws}/members/{eu.id}")).status_code == 204
    assert (await e.get(f"{API}/workspaces/{ws}")).status_code == 404
    # no longer a member: cannot leave again
    assert (await e.delete(f"{API}/workspaces/{ws}/members/{eu.id}")).status_code == 404


async def test_last_owner_protected(login):
    ou, a = await login("a@example.com")
    bu, b = await login("b@example.com")
    ws = await _shared(a)
    assert (await a.patch(f"{API}/workspaces/{ws}/members/{ou.id}", json={"role": "editor"})).status_code == 409
    assert (await a.delete(f"{API}/workspaces/{ws}/members/{ou.id}")).status_code == 409
    await join_workspace(a, b, ws, "b@example.com", "viewer")
    assert (await a.patch(f"{API}/workspaces/{ws}/members/{bu.id}", json={"role": "owner"})).status_code == 200
    # two owners: one may step down, the remaining one is protected
    assert (await a.patch(f"{API}/workspaces/{ws}/members/{ou.id}", json={"role": "editor"})).status_code == 200
    assert (await b.patch(f"{API}/workspaces/{ws}/members/{bu.id}", json={"role": "viewer"})).status_code == 409
    assert (await b.delete(f"{API}/workspaces/{ws}/members/{bu.id}")).status_code == 409
    assert (await b.delete(f"{API}/workspaces/{ws}/members/{ou.id}")).status_code == 204


async def test_personal_workspace_rejected(login):
    ou, a = await login("a@example.com")
    await login("b@example.com")
    ws = next(w["id"] for w in (await a.get(f"{API}/workspaces")).json() if w["kind"] == "personal")
    assert (await _invite(a, ws, "b@example.com")).status_code == 400
    assert (await a.patch(f"{API}/workspaces/{ws}/members/{ou.id}", json={"role": "viewer"})).status_code == 400
    assert (await a.delete(f"{API}/workspaces/{ws}/members/{ou.id}")).status_code == 400


async def test_pat_cannot_manage_membership(login, pat_client):
    ou, a = await login("a@example.com")
    _, b = await login("b@example.com")
    ws = await _shared(a)
    await _invite(a, ws, "b@example.com")
    inv = (await _my_invites(b))[0]["id"]
    pa = await pat_client(a)
    pb = await pat_client(b)
    assert (await pa.post(f"{API}/workspaces", json={"name": "x"})).status_code == 403
    assert (await pa.post(f"{API}/workspaces/{ws}/members", json={"email": "z@example.com"})).status_code == 403
    assert (await pa.get(f"{API}/workspaces/{ws}/invitations")).status_code == 403
    assert (await pa.patch(f"{API}/workspaces/{ws}/members/{ou.id}", json={"role": "owner"})).status_code == 403
    assert (await pa.delete(f"{API}/workspaces/{ws}/members/{ou.id}")).status_code == 403
    assert (await pb.get(f"{API}/invitations")).status_code == 403
    assert (await pb.post(f"{API}/invitations/{inv}/accept")).status_code == 403
    assert (await pb.post(f"{API}/invitations/{inv}/decline")).status_code == 403
    # read access via PAT still works
    assert (await pa.get(f"{API}/workspaces")).status_code == 200


async def test_invite_rate_limit(login):
    _, a = await login("a@example.com")
    ws = await _shared(a)
    for i in range(30):
        assert (await _invite(a, ws, f"u{i}@example.com")).status_code == 202
    r = await _invite(a, ws, "u30@example.com")
    assert r.status_code == 429 and "retry-after" in r.headers
