from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.core.security import hash_token
from app.db.session import get_sessionmaker
from app.models import ApiKey

API = "/api/v1"


async def _create(c, **over):
    body = {"name": "cli", "scopes": ["read:papers"], "expires_in_days": 30, **over}
    r = await c.post(f"{API}/tokens", json=body)
    assert r.status_code == 201, r.text
    return r.json()


async def test_create_returns_plaintext_once_and_stores_hash_only(login):
    _, c = await login("a@example.com")
    t = await _create(c)
    assert t["token"].startswith("bf_pat_") and t["token_prefix"] == t["token"][:12]
    assert t["scopes"] == ["read:papers"] and t["expires_at"]
    listing = (await c.get(f"{API}/tokens")).json()
    assert len(listing) == 1
    assert "token" not in listing[0] and "token_hash" not in listing[0]
    assert t["token"] not in (await c.get(f"{API}/tokens")).text
    async with get_sessionmaker()() as db:
        key = (await db.execute(select(ApiKey))).scalar_one()
    assert key.token_hash == hash_token(t["token"])
    assert t["token"] not in (key.token_hash, key.token_prefix, key.name)


async def test_pat_authenticates_without_cookie_or_origin(login, make_client):
    _, c = await login("b@example.com")
    t = await _create(c)
    p = make_client(origin=None)
    r = await p.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {t['token']}"})
    assert r.status_code == 200 and r.json()["auth_method"] == "pat" and r.json()["scopes"] == ["read:papers"]
    async with get_sessionmaker()() as db:
        assert (await db.execute(select(ApiKey))).scalar_one().last_used_at is not None
    assert (await p.get(f"{API}/workspaces", headers={"Authorization": f"Bearer {t['token']}"})).status_code == 200


async def test_pat_invalid_variants(login, make_client):
    _, c = await login("c@example.com")
    t = await _create(c)
    p = make_client(origin=None)
    for h in (
        f"Bearer {t['token']}x",
        "Bearer bf_pat_doesnotexist",
        "Bearer notapat",
        f"Basic {t['token']}",
        "Bearer ",
    ):
        assert (await p.get(f"{API}/auth/me", headers={"Authorization": h})).status_code == 401, h


async def test_pat_revoke(login, make_client):
    _, c = await login("d@example.com")
    t = await _create(c)
    p = make_client(origin=None)
    h = {"Authorization": f"Bearer {t['token']}"}
    assert (await p.get(f"{API}/auth/me", headers=h)).status_code == 200
    assert (await c.delete(f"{API}/tokens/{t['id']}")).status_code == 204
    assert (await p.get(f"{API}/auth/me", headers=h)).status_code == 401
    assert (await c.get(f"{API}/tokens")).json()[0]["revoked_at"] is not None


async def test_pat_expired(login, make_client):
    _, c = await login("e@example.com")
    t = await _create(c)
    async with get_sessionmaker()() as db:
        await db.execute(update(ApiKey).values(expires_at=datetime.now(UTC) - timedelta(seconds=1)))
        await db.commit()
    p = make_client(origin=None)
    assert (await p.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {t['token']}"})).status_code == 401


async def test_pat_never_expires_option(login):
    _, c = await login("f@example.com")
    t = await _create(c, expires_in_days=None)
    assert t["expires_at"] is None


async def test_pat_inactive_user_rejected(login, make_client):
    user, c = await login("g@example.com")
    t = await _create(c)
    async with get_sessionmaker()() as db:
        u = await db.get(type(user), user.id)
        u.is_active = False
        await db.commit()
    p = make_client(origin=None)
    assert (await p.get(f"{API}/auth/me", headers={"Authorization": f"Bearer {t['token']}"})).status_code == 401


async def test_scope_enforcement(login, make_client):
    _, c = await login("h@example.com")
    ro = await _create(c, scopes=["read:papers"])
    rw = await _create(c, scopes=["read:papers", "write:papers"])
    p = make_client(origin=None)
    h_ro = {"Authorization": f"Bearer {ro['token']}"}
    h_rw = {"Authorization": f"Bearer {rw['token']}"}
    assert (await p.get(f"{API}/workspaces", headers=h_ro)).status_code == 200
    r = await p.post(f"{API}/workspaces", json={"name": "Lab"}, headers=h_ro)
    assert r.status_code == 403
    assert (await p.post(f"{API}/workspaces", json={"name": "Lab"}, headers=h_rw)).status_code == 201
    wo = await _create(c, scopes=["write:papers"])
    assert (await p.get(f"{API}/workspaces", headers={"Authorization": f"Bearer {wo['token']}"})).status_code == 403


async def test_pat_cannot_manage_tokens(login, make_client):
    _, c = await login("i@example.com")
    t = await _create(c, scopes=["read:papers", "write:papers"])
    p = make_client(origin=None)
    h = {"Authorization": f"Bearer {t['token']}"}
    assert (await p.post(f"{API}/tokens", json={"name": "x", "scopes": ["read:papers"]}, headers=h)).status_code == 403
    assert (await p.get(f"{API}/tokens", headers=h)).status_code == 403
    assert (await p.delete(f"{API}/tokens/{t['id']}", headers=h)).status_code == 403
    assert (await p.post(f"{API}/auth/logout", headers=h)).status_code == 403


async def test_token_validation_and_ownership(login):
    _, a = await login("j@example.com")
    _, b = await login("k@example.com")
    assert (await a.post(f"{API}/tokens", json={"name": "x", "scopes": ["admin"]})).status_code == 422
    assert (await a.post(f"{API}/tokens", json={"name": "x", "scopes": []})).status_code == 422
    t = await _create(a)
    assert (await b.delete(f"{API}/tokens/{t['id']}")).status_code == 404
    assert (await b.get(f"{API}/tokens")).json() == []
    assert (await a.get(f"{API}/tokens")).json()[0]["revoked_at"] is None


async def test_tokens_require_auth(client):
    assert (await client.get(f"{API}/tokens")).status_code == 401
