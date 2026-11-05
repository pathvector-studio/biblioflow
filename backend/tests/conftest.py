import asyncio
import os
from urllib.parse import urlsplit, urlunsplit

import pytest
import pytest_asyncio

ORIGIN = "https://app.example.com"
TEST_DB = os.environ.get("TEST_DATABASE_URL")


def _redis_test_url() -> str:
    base = os.environ.get("REDIS_URL")
    if not base:
        return "redis://localhost:6379/15"
    parts = urlsplit(base)
    return urlunsplit(parts._replace(path="/15"))


# Must be configured before `app` modules are imported (Settings is cached).
os.environ["DATABASE_URL"] = TEST_DB or "postgresql+asyncpg://u:p@localhost:1/none"
os.environ["REDIS_URL"] = _redis_test_url()
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40
os.environ["CORS_ALLOWED_ORIGINS"] = ORIGIN
os.environ["ALLOW_REGISTRATION"] = "false"
os.environ["MAX_UPLOAD_MB"] = "1"
os.environ["URL_IMPORT_ENABLED"] = "true"  # feature is off by default; tests cover it explicitly
os.environ["FILENAME_TEMPLATE"] = "{year}/{identifier}_{slug_title}.pdf"
os.environ["EMBEDDING_PROVIDER"] = "none"  # tests that need vectors patch embedding.get_embedder

TABLES = (
    "paper_collections, collections, paper_tags, tags, annotations, paper_chunks, papers, "
    "workspace_invitations, workspace_members, workspaces, api_keys, users"
)


def _run_alembic(action: str) -> None:
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(os.path.dirname(__file__), "..", "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(os.path.dirname(__file__), "..", "alembic"))
    if action == "up":
        command.downgrade(cfg, "base")
        command.upgrade(cfg, "head")


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def infra():
    if not TEST_DB:
        pytest.skip("TEST_DATABASE_URL not set")
    from sqlalchemy import text

    from app.core.redis import get_redis
    from app.db.session import get_engine

    try:
        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        await get_redis().ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"test infrastructure unreachable: {exc!r}")
    await asyncio.to_thread(_run_alembic, "up")
    yield
    from app.core.redis import close_redis
    from app.db.session import dispose_engine

    await close_redis()
    await dispose_engine()


@pytest_asyncio.fixture(loop_scope="session")
async def clean(infra):
    from sqlalchemy import text

    from app.core.redis import get_redis
    from app.db.session import get_engine

    async with get_engine().begin() as conn:
        await conn.execute(text(f"TRUNCATE {TABLES} CASCADE"))
    await get_redis().flushdb()
    yield


@pytest_asyncio.fixture(loop_scope="session")
async def make_client(clean):
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    clients = []

    def _make(origin: str | None = ORIGIN) -> AsyncClient:
        headers = {"Origin": origin} if origin else {}
        c = AsyncClient(transport=ASGITransport(app=app), base_url="https://testserver", headers=headers)
        clients.append(c)
        return c

    yield _make
    for c in clients:
        await c.aclose()


@pytest_asyncio.fixture(loop_scope="session")
async def client(make_client):
    return make_client()


@pytest_asyncio.fixture(loop_scope="session")
async def create_user(clean):
    from app.db.session import get_sessionmaker
    from app.services.users import create_user as svc

    async def _create(email: str, password: str = "correct-horse-battery", admin: bool = False):
        async with get_sessionmaker()() as db:
            user = await svc(db, email=email, password=password, is_admin=admin)
            await db.commit()
            return user

    return _create


@pytest_asyncio.fixture(loop_scope="session")
async def login(create_user, make_client):
    """Create a user and return (user, client logged in via cookie)."""

    async def _login(email: str, password: str = "correct-horse-battery"):
        user = await create_user(email, password)
        c = make_client()
        r = await c.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return user, c

    return _login


# ---------------------------------------------------------------- Phase 2 fixtures
@pytest.fixture
def resolver():
    from app.main import app
    from app.services.metadata import get_resolver

    from tests.helpers import FakeResolver, make_meta

    fake = FakeResolver()
    fake.add(make_meta())
    app.dependency_overrides[get_resolver] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_resolver, None)


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Per-test library_root / watch_dir under tmp (applied to the cached Settings object)."""
    from types import SimpleNamespace

    from app.core.config import get_settings

    s = get_settings()
    lib, watch = tmp_path / "library", tmp_path / "watch"
    lib.mkdir()
    watch.mkdir()
    monkeypatch.setattr(s, "library_root", lib)
    monkeypatch.setattr(s, "watch_dir", watch)
    return SimpleNamespace(library=lib, watch=watch)


@pytest.fixture
def enqueued(monkeypatch):
    """Replace the Arq pool; ``enqueued.jobs`` records ``(function, *args)`` tuples."""
    from app.core import queue

    from tests.helpers import FakePool

    pool = FakePool()

    async def _get():
        return pool

    monkeypatch.setattr(queue, "get_arq_pool", _get)
    return pool


@pytest_asyncio.fixture(loop_scope="session")
async def pat_client(make_client):
    """Return ``async (session_client, scopes) -> client`` authenticating with a fresh PAT (no cookie/Origin)."""

    async def _make(session_client, scopes=("read:papers", "write:papers")):
        r = await session_client.post(
            "/api/v1/tokens", json={"name": "t", "scopes": list(scopes), "expires_in_days": 1}
        )
        assert r.status_code == 201, r.text
        c = make_client(origin=None)
        c.headers["Authorization"] = f"Bearer {r.json()['token']}"
        return c

    return _make
