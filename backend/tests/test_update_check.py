"""Opt-in update notice: the daily check, GET /system/update-status, and the SSRF guard (needs the test database)."""

import asyncio
import json
import socket

import httpx
import pytest

from app import __version__
from app.core.config import get_settings
from app.core.net import BlockedDestination
from app.core.redis import get_redis
from app.services import update_check
from app.workers.tasks import check_for_update

URL = "/api/v1/system/update-status"
API = "/api/v1"


class FakeGitHub:
    """Stands in for GitHub at the HTTP layer; counts and records every request."""

    def __init__(self, payload) -> None:
        self.payload = payload
        self.requests: list[httpx.Request] = []

    def client(self, **kw) -> httpx.AsyncClient:
        def handler(req: httpx.Request) -> httpx.Response:
            self.requests.append(req)
            return httpx.Response(200, json=self.payload)

        c = httpx.AsyncClient(transport=httpx.MockTransport(handler), headers=kw.get("headers"))
        c.allowed_hosts = kw.get("allowed_hosts")  # type: ignore[attr-defined]
        return c


def bump(kind: str) -> str:
    major, minor, patch = (int(x) for x in __version__.split("."))
    return {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0", "patch": f"{major}.{minor}.{patch + 1}"}[kind]


def release(tag: str, body: str = "") -> dict:
    return {
        "tag_name": tag,
        "html_url": f"https://github.com/pathvector-studio/biblioflow/releases/tag/{tag}",
        "published_at": "2026-11-20T00:00:00Z",
        "body": body,
    }


@pytest.fixture
def enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "update_check", True)


@pytest.fixture
def fake(monkeypatch):
    def _install(payload) -> FakeGitHub:
        gh = FakeGitHub(payload)
        monkeypatch.setattr(update_check, "safe_client", gh.client)
        return gh

    return _install


async def _admin(create_user, make_client, email="root@example.com"):
    await create_user(email, admin=True)
    c = make_client()
    r = await c.post(f"{API}/auth/login", json={"email": email, "password": "correct-horse-battery"})
    assert r.status_code == 200, r.text
    return c


async def test_disabled_makes_no_request_and_reports_disabled(create_user, make_client, fake):
    gh = fake(release("v99.0.0"))
    assert get_settings().update_check is False
    assert await check_for_update({}) is False
    assert gh.requests == []
    c = await _admin(create_user, make_client)
    body = (await c.get(URL)).json()
    assert body == {
        "enabled": False, "current": __version__, "latest": None, "update_available": False,
        "security": False, "url": None, "checked_at": None,
    }


async def test_disabled_ignores_a_stale_stored_value(create_user, make_client, enabled, fake, monkeypatch):
    fake(release("v99.0.0"))
    assert await check_for_update({}) is True
    monkeypatch.setattr(get_settings(), "update_check", False)
    c = await _admin(create_user, make_client)
    assert (await c.get(URL)).json()["enabled"] is False
    assert (await c.get(URL)).json()["latest"] is None


async def test_newer_release_with_security_section(create_user, make_client, enabled, fake):
    gh = fake(release(f"v{bump('minor')}", "## Notes\n\n### Added\n- x\n\n### Security\n- fixed y\n"))
    assert await check_for_update({}) is True
    assert len(gh.requests) == 1
    req = gh.requests[0]
    assert str(req.url) == update_check.RELEASE_API_URL and req.method == "GET"
    assert req.headers["user-agent"] == "BiblioFlow" and req.headers["accept"] == "application/vnd.github+json"
    assert not {"authorization", "cookie"} & set(req.headers)
    c = await _admin(create_user, make_client)
    body = (await c.get(URL)).json()
    assert body["enabled"] is True and body["update_available"] is True and body["security"] is True
    assert body["latest"] == bump("minor") and body["current"] == __version__
    assert body["url"].startswith("https://github.com/pathvector-studio/biblioflow/releases/") and body["checked_at"]


async def test_newer_release_without_security_section(create_user, make_client, enabled, fake):
    fake(release(f"v{bump('patch')}", "### Fixed\n- a mention of Security in prose is not a heading\n"))
    await check_for_update({})
    body = (await (await _admin(create_user, make_client)).get(URL)).json()
    assert body["update_available"] is True and body["security"] is False


async def test_same_or_older_is_not_an_update(create_user, make_client, enabled, fake):
    c = await _admin(create_user, make_client)
    gh = fake(release(f"v{__version__}", "### Security\n- x\n"))
    await check_for_update({})
    body = (await c.get(URL)).json()
    assert body["update_available"] is False and body["security"] is False and body["latest"] == __version__
    gh.payload = release("v0.0.1")
    await check_for_update({})
    assert (await c.get(URL)).json()["update_available"] is False


def test_versions_compare_numerically() -> None:
    assert update_check.parse_version("0.10.0") > update_check.parse_version("0.9.0")
    for bad in ("v1.2", "v1.2.3-rc1", "latest", "v1.2.3.4", "va.b.c", "v1.2.3\n", ""):
        assert update_check.parse_version(bad) is None


@pytest.mark.parametrize("tag", ["v99.0.0-rc1", "nightly", "99.0.0", "v99", None, 7])
async def test_non_semver_tag_is_ignored(create_user, make_client, enabled, fake, tag):
    fake(release("v99.0.0"))
    await check_for_update({})  # a good value is stored first
    fake({**release("x"), "tag_name": tag})
    assert await check_for_update({}) is False
    body = (await (await _admin(create_user, make_client)).get(URL)).json()
    assert body["latest"] == "99.0.0"  # previous value kept


async def test_release_url_outside_the_project_is_dropped(create_user, make_client, enabled, fake):
    fake({**release("v99.0.0"), "html_url": "https://evil.example/phish"})
    await check_for_update({})
    assert (await (await _admin(create_user, make_client)).get(URL)).json()["url"] is None


async def test_stored_value_expires(enabled, fake):
    fake(release("v99.0.0"))
    await check_for_update({})
    assert 0 < await get_redis().ttl(update_check.REDIS_KEY) <= update_check.TTL_SECONDS
    json.loads(await get_redis().get(update_check.REDIS_KEY))


async def test_only_admins_see_it_and_not_with_a_token(login, create_user, make_client, enabled, fake):
    _, member = await login("member@example.com")
    assert (await member.get(URL)).status_code == 403
    assert (await make_client().get(URL)).status_code == 401
    admin = await _admin(create_user, make_client)
    assert (await admin.get(URL)).status_code == 200
    tok = (await admin.post(f"{API}/tokens", json={"name": "cli", "scopes": ["read:papers"], "expires_in_days": 1})).json()["token"]
    r = await make_client(origin=None).get(URL, headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 403


async def test_private_address_is_refused(enabled, monkeypatch):
    """The real safe_client: api.github.com resolving to a private address is never connected to."""
    connections = 0

    async def handler(reader, writer):
        nonlocal connections
        connections += 1
        writer.close()

    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    loop = asyncio.get_running_loop()

    async def fake_getaddrinfo(host, port, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]

    monkeypatch.setattr(loop, "getaddrinfo", fake_getaddrinfo)
    try:
        with pytest.raises(BlockedDestination):
            await update_check.fetch_latest_release()
        assert await update_check.run_update_check(get_redis(), enabled=True) is False  # logged, not raised
        await asyncio.sleep(0.05)
    finally:
        server.close()
        await server.wait_closed()
    assert connections == 0
    assert await get_redis().get(update_check.REDIS_KEY) is None


async def test_other_hosts_are_not_allowed(monkeypatch):
    seen: list[str] = []
    gh = FakeGitHub(release("v99.0.0"))
    monkeypatch.setattr(update_check, "safe_client", lambda **kw: (seen.append(str(kw["allowed_hosts"])), gh.client(**kw))[1])
    monkeypatch.setattr(update_check, "RELEASE_API_URL", "https://evil.example/repos/x/releases/latest")
    with pytest.raises(BlockedDestination):
        await update_check.fetch_latest_release()
    assert gh.requests == [] and seen == ["{'api.github.com'}"]
