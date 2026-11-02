import asyncio
from datetime import timedelta

import jwt
import pytest
from sqlalchemy import select

from app.core.security import create_access_token, decode_access_token
from app.db.session import get_sessionmaker
from app.models import Workspace, WorkspaceMember

from conftest import ORIGIN

API = "/api/v1"
PW = "correct-horse-battery"


async def test_health(client):
    r = await client.get(f"{API}/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    r = await client.get(f"{API}/readyz")
    assert r.status_code == 200 and r.json()["checks"] == {"db": "ok", "redis": "ok"}
    assert r.headers["x-content-type-options"] == "nosniff"


async def test_login_success_and_cookie_attributes(client, create_user):
    await create_user("Alice@Example.com")
    r = await client.post(f"{API}/auth/login", json={"email": "alice@example.com", "password": PW})
    assert r.status_code == 200
    body = r.json()
    assert body["email"] == "alice@example.com" and "password_hash" not in body
    cookie = r.headers["set-cookie"].lower()
    assert cookie.startswith("bf_session=")
    for attr in ("httponly", "secure", "samesite=lax", "path=/", "max-age=43200"):
        assert attr in cookie
    me = await client.get(f"{API}/auth/me")
    assert me.status_code == 200
    assert me.json()["auth_method"] == "session"
    assert me.json()["user"]["email"] == "alice@example.com"


async def test_login_failures_are_uniform(client, create_user):
    await create_user("bob@example.com")
    bad_pw = await client.post(f"{API}/auth/login", json={"email": "bob@example.com", "password": "nope-nope-nope"})
    no_user = await client.post(f"{API}/auth/login", json={"email": "ghost@example.com", "password": PW})
    assert bad_pw.status_code == no_user.status_code == 401
    assert bad_pw.json() == no_user.json()
    assert "set-cookie" not in bad_pw.headers


async def test_inactive_user_cannot_login(client, create_user):
    user = await create_user("off@example.com")
    async with get_sessionmaker()() as db:
        u = await db.get(type(user), user.id)
        u.is_active = False
        await db.commit()
    r = await client.post(f"{API}/auth/login", json={"email": "off@example.com", "password": PW})
    assert r.status_code == 401


async def test_me_requires_auth(client):
    assert (await client.get(f"{API}/auth/me")).status_code == 401
    client.cookies.set("bf_session", "garbage")
    assert (await client.get(f"{API}/auth/me")).status_code == 401


async def test_logout_revokes_session(login, make_client):
    _, c = await login("carol@example.com")
    token = c.cookies.get("bf_session")
    assert token
    assert (await c.get(f"{API}/auth/me")).status_code == 200
    r = await c.post(f"{API}/auth/logout")
    assert r.status_code == 204
    assert "bf_session" in r.headers["set-cookie"] and "max-age=0" in r.headers["set-cookie"].lower()
    # replay the old token with a fresh client
    c2 = make_client()
    c2.cookies.set("bf_session", token)
    assert (await c2.get(f"{API}/auth/me")).status_code == 401


async def test_expired_and_tampered_cookie(login, make_client):
    user, _ = await login("dave@example.com")
    expired, _, _ = create_access_token(user.id, expires_delta=timedelta(seconds=-10))
    valid, _, _ = create_access_token(user.id)
    head, payload, sig = valid.split(".")
    tampered = f"{head}.{payload}.{('A' if sig[0] != 'A' else 'B') + sig[1:]}"
    forged = jwt.encode({"sub": str(user.id), "jti": "x", "iat": 0, "exp": 9999999999, "typ": "session"}, "wrong" * 10, algorithm="HS256")
    for tok in (expired, tampered, forged):
        c = make_client()
        c.cookies.set("bf_session", tok)
        assert (await c.get(f"{API}/auth/me")).status_code == 401
    c = make_client()
    c.cookies.set("bf_session", valid)
    assert (await c.get(f"{API}/auth/me")).status_code == 200


async def test_login_rate_limit_per_ip_email(client, create_user):
    await create_user("erin@example.com")
    for _ in range(10):
        r = await client.post(f"{API}/auth/login", json={"email": "erin@example.com", "password": "bad-password-1"})
        assert r.status_code == 401
    r = await client.post(f"{API}/auth/login", json={"email": "erin@example.com", "password": PW})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) >= 1
    # different email from the same IP is unaffected (per ip+email bucket)
    r = await client.post(f"{API}/auth/login", json={"email": "other@example.com", "password": PW})
    assert r.status_code == 401


async def test_login_rate_limit_per_ip(client):
    for i in range(30):
        r = await client.post(f"{API}/auth/login", json={"email": f"u{i}@example.com", "password": "bad-password-1"})
        assert r.status_code == 401
    r = await client.post(f"{API}/auth/login", json={"email": "u99@example.com", "password": "bad-password-1"})
    assert r.status_code == 429 and "retry-after" in r.headers


async def test_register_disabled_by_default(client):
    r = await client.post(f"{API}/auth/register", json={"email": "x@example.com", "password": PW})
    assert r.status_code == 403


async def test_register_creates_personal_workspace(client, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "allow_registration", True)
    r = await client.post(f"{API}/auth/register", json={"email": "new@example.com", "password": PW, "display_name": "New"})
    assert r.status_code == 201 and r.json()["display_name"] == "New"
    dup = await client.post(f"{API}/auth/register", json={"email": "NEW@example.com", "password": PW})
    assert dup.status_code == 409
    weak = await client.post(f"{API}/auth/register", json={"email": "w@example.com", "password": "short"})
    assert weak.status_code == 422
    async with get_sessionmaker()() as db:
        rows = (await db.execute(select(Workspace, WorkspaceMember).join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id))).all()
    assert len(rows) == 1 and rows[0][0].kind.value == "personal" and rows[0][1].role.value == "owner"


async def test_csrf_login_not_required_but_logout_is(login, make_client):
    _, c = await login("frank@example.com")
    evil = await c.post(f"{API}/auth/logout", headers={"Origin": "https://evil.example"})
    assert evil.status_code == 403
    assert (await c.get(f"{API}/auth/me")).status_code == 200  # session survives


async def test_security_headers_and_cors(client):
    r = await client.options(
        f"{API}/auth/login",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert r.headers["access-control-allow-origin"] == ORIGIN
    assert r.headers["access-control-allow-credentials"] == "true"
    r = await client.options(
        f"{API}/auth/login",
        headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in r.headers
    r = await client.get(f"{API}/healthz")
    assert r.headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" in r.headers


async def test_oversized_body_rejected(client):
    r = await client.post(f"{API}/auth/login", content=b"x" * (1024 * 1024 + 1), headers={"Content-Type": "application/json"})
    assert r.status_code == 413


async def test_docs_disabled(client):
    assert (await client.get("/api/docs")).status_code == 404
    assert (await client.get("/api/openapi.json")).status_code == 404


async def test_cli_create_user_service_makes_workspace(create_user):
    user = await create_user("cli@example.com", admin=True)
    assert user.is_admin
    async with get_sessionmaker()() as db:
        ws = (await db.execute(select(Workspace).where(Workspace.owner_id == user.id))).scalars().all()
    assert len(ws) == 1 and ws[0].slug.startswith("personal-")
