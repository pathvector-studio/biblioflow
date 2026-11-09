"""GET /api/v1/chat/status (needs the test database)."""

from app.core.config import Settings
from app.services import llm
from app.services.llm import FakeLLM

URL = "/api/v1/chat/status"
KEY = "sk-super-secret-value"


async def test_requires_authentication(client):
    assert (await client.get(URL)).status_code == 401


async def test_disabled_by_default(login):
    _, c = await login("a@example.com")
    r = await c.get(URL)
    assert r.status_code == 200
    assert r.json() == {
        "enabled": False,
        "provider": "none",
        "model": None,
        "vision_available": False,
        "daily_requests_remaining": None,
    }


async def test_enabled_reports_model_and_remaining(login, monkeypatch):
    user, c = await login("a@example.com")
    monkeypatch.setattr(llm, "get_client", lambda: FakeLLM(model_id="claude-sonnet-5-5"))
    body = (await c.get(URL)).json()
    assert body["enabled"] is True and body["model"] == "claude-sonnet-5-5" and body["vision_available"] is True
    assert body["daily_requests_remaining"] == 200

    from app.core.redis import get_redis

    await get_redis().set(llm.daily_requests_key(user.id), 5)
    assert (await c.get(URL)).json()["daily_requests_remaining"] == 195
    await get_redis().delete(llm.daily_requests_key(user.id))

    monkeypatch.setattr(llm, "get_client", lambda: FakeLLM(vision=False))
    assert (await c.get(URL)).json()["vision_available"] is False


async def test_no_secrets_in_the_body(login, monkeypatch):
    _, c = await login("a@example.com")
    s = Settings(
        _env_file=None,
        database_url="postgresql+asyncpg://u:p@h/d",
        redis_url="redis://h",
        jwt_secret="x" * 40,
        llm_provider="openai_compatible",
        llm_model="m",
        llm_api_key=KEY,
        llm_api_base="http://internal-gpu.example:8000/v1",
    )
    monkeypatch.setattr(llm, "get_settings", lambda: s)
    monkeypatch.setattr("app.api.v1.chat.get_settings", lambda: s)
    r = await c.get(URL)
    assert r.status_code == 200 and r.json()["provider"] == "openai_compatible"
    assert KEY not in r.text and "internal-gpu" not in r.text and "api_base" not in r.text.lower()


async def test_incomplete_settings_report_disabled(login, monkeypatch):
    """LLM_PROVIDER=openai without a key (an old .env): chat is reported off instead of the API failing to start."""
    from app.api.v1 import chat as chat_api
    from app.core.config import get_settings

    s = get_settings().model_copy(update={"llm_provider": "openai", "llm_model": "gpt-4o-mini", "llm_api_key": None})
    monkeypatch.setattr(chat_api, "get_settings", lambda: s)
    monkeypatch.setattr(llm, "get_client", lambda: llm.build_client(s))
    _, c = await login("a@example.com")
    body = (await c.get(URL)).json()
    assert body["enabled"] is False and body["provider"] == "openai" and body["model"] is None
