import hashlib
import time
import uuid
from datetime import timedelta

import jwt
import pytest
from fastapi import FastAPI
from starlette.requests import Request
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.core.security import (
    PAT_PREFIX,
    TokenError,
    create_access_token,
    decode_access_token,
    generate_pat,
    hash_password,
    hash_token,
    request_origin_allowed,
    validate_password,
    verify_password,
)

SECRET = "unit-test-secret-" + "k" * 40


def test_password_argon2id_roundtrip():
    h = hash_password("correct-horse-battery")
    assert h.startswith("$argon2id$")
    assert verify_password("correct-horse-battery", h)
    assert not verify_password("wrong-password-123", h)
    assert not verify_password("anything", None)
    assert not verify_password("anything", "not-a-hash")


def test_password_policy():
    with pytest.raises(ValueError):
        validate_password("short")
    assert validate_password("long-enough-pass")


def test_pat_format_and_hash():
    token, prefix, digest = generate_pat()
    assert token.startswith(PAT_PREFIX)
    assert len(token) >= len(PAT_PREFIX) + 43
    assert prefix == token[:12]
    assert digest == hashlib.sha256(token.encode()).hexdigest() == hash_token(token)
    assert token not in digest
    assert generate_pat()[0] != token


def test_jwt_roundtrip():
    uid = uuid.uuid4()
    token, jti, exp = create_access_token(uid, secret=SECRET)
    claims = decode_access_token(token, secret=SECRET)
    assert claims.sub == uid and claims.jti == jti
    raw = jwt.decode(token, SECRET, algorithms=["HS256"])
    assert raw["typ"] == "session" and set(raw) >= {"sub", "jti", "iat", "exp", "typ"}


def test_jwt_expired():
    token, _, _ = create_access_token(uuid.uuid4(), secret=SECRET, expires_delta=timedelta(seconds=-5))
    with pytest.raises(TokenError):
        decode_access_token(token, secret=SECRET)


def test_jwt_tampered_and_wrong_secret():
    token, _, _ = create_access_token(uuid.uuid4(), secret=SECRET)
    head, payload, sig = token.split(".")
    flipped = ("A" if sig[0] != "A" else "B") + sig[1:]
    with pytest.raises(TokenError):
        decode_access_token(f"{head}.{payload}.{flipped}", secret=SECRET)
    with pytest.raises(TokenError):
        decode_access_token(token, secret="another-secret-" + "z" * 40)
    with pytest.raises(TokenError):
        decode_access_token("garbage", secret=SECRET)


def test_jwt_alg_none_and_wrong_typ_and_missing_claims():
    now = int(time.time())
    base = {"sub": str(uuid.uuid4()), "jti": "j", "iat": now, "exp": now + 100, "typ": "session"}
    with pytest.raises(TokenError):
        decode_access_token(jwt.encode(base, key="", algorithm="none"), secret=SECRET)
    with pytest.raises(TokenError):
        decode_access_token(jwt.encode({**base, "typ": "access"}, SECRET, algorithm="HS256"), secret=SECRET)
    for missing in ("jti", "exp", "sub"):
        claims = {k: v for k, v in base.items() if k != missing}
        with pytest.raises(TokenError):
            decode_access_token(jwt.encode(claims, SECRET, algorithm="HS256"), secret=SECRET)
    with pytest.raises(TokenError):
        decode_access_token(jwt.encode({**base, "sub": "not-a-uuid"}, SECRET, algorithm="HS256"), secret=SECRET)


def test_origin_check():
    allowed = ["https://app.example.com"]
    assert request_origin_allowed("https://app.example.com", None, allowed)
    assert request_origin_allowed("https://APP.example.com/", None, allowed)
    assert not request_origin_allowed("https://evil.example", None, allowed)
    assert not request_origin_allowed("null", None, allowed)
    assert not request_origin_allowed("https://evil.example", "https://app.example.com/x", allowed)  # Origin wins
    assert request_origin_allowed(None, "https://app.example.com/some/page?x=1", allowed)
    assert not request_origin_allowed(None, "https://app.example.com.evil.io/", allowed)
    assert not request_origin_allowed(None, None, allowed)
    assert not request_origin_allowed(None, "garbage", allowed)
    assert not request_origin_allowed("https://app.example.com", None, [])


def _env(**over):
    base = {
        "DATABASE_URL": "postgresql+asyncpg://u:p@h/d",
        "REDIS_URL": "redis://h:6379/0",
        "JWT_SECRET": "s" * 40,
    }
    base.update(over)
    return base


def test_settings_validation(monkeypatch):
    for k in list(__import__("os").environ):
        if k.upper() in {"DATABASE_URL", "REDIS_URL", "JWT_SECRET", "CORS_ALLOWED_ORIGINS", "FILENAME_TEMPLATE", "MAX_UPLOAD_MB"}:
            monkeypatch.delenv(k)
    with pytest.raises(ValidationError):
        Settings(**{k.lower(): v for k, v in _env(JWT_SECRET="short").items()})
    with pytest.raises(ValidationError):
        Settings(**{k.lower(): v for k, v in _env(DATABASE_URL="postgresql://u:p@h/d").items()})
    with pytest.raises(ValidationError):
        Settings(**{k.lower(): v for k, v in _env(FILENAME_TEMPLATE="{identifier.__class__}.pdf").items()})
    with pytest.raises(ValidationError):
        Settings(**{k.lower(): v for k, v in _env(CORS_ALLOWED_ORIGINS="*").items()})
    with pytest.raises(ValidationError):
        Settings(**{k.lower(): v for k, v in _env(EMBEDDING_DIM="768").items()})
    s = Settings(**{k.lower(): v for k, v in _env(CORS_ALLOWED_ORIGINS="https://a.example, https://b.example/").items()})
    assert s.cors_allowed_origins == ["https://a.example", "https://b.example"]
    assert "s" * 40 not in repr(s)
    assert s.max_upload_bytes == 50 * 1024 * 1024


def test_settings_env_names_case_insensitive(monkeypatch):
    monkeypatch.setenv("database_url", "postgresql+asyncpg://u:p@h/d")
    monkeypatch.setenv("REDIS_URL", "redis://h/0")
    monkeypatch.setenv("JWT_SECRET", "q" * 40)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", "https://x.example,https://y.example")
    s = Settings()
    assert s.cors_allowed_origins == ["https://x.example", "https://y.example"]


# ------------------------------------------------------------ middleware
def _echo_app(max_bytes: int) -> FastAPI:
    app = FastAPI()

    @app.post("/echo")
    async def echo(request: Request):
        return {"n": len(await request.body())}

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=max_bytes)
    app.add_middleware(SecurityHeadersMiddleware)
    return app



async def test_body_limit_content_length():
    async with AsyncClient(transport=ASGITransport(app=_echo_app(1000)), base_url="http://t") as c:
        assert (await c.post("/echo", content=b"x" * 1000)).status_code == 200
        r = await c.post("/echo", content=b"x" * 1001)
        assert r.status_code == 413


async def test_body_limit_streamed_without_content_length():
    async def gen():
        for _ in range(20):
            yield b"x" * 100

    async with AsyncClient(transport=ASGITransport(app=_echo_app(1000)), base_url="http://t") as c:
        r = await c.post("/echo", content=gen())
        assert r.status_code == 413

        async def small():
            yield b"x" * 500

        assert (await c.post("/echo", content=small())).json() == {"n": 500}


async def test_body_limit_per_path():
    app = FastAPI()

    @app.post("/echo")
    @app.post("/upload")
    async def echo(request: Request):
        return {"n": len(await request.body())}

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=100, path_limits={"/upload": 1000})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        assert (await c.post("/echo", content=b"x" * 101)).status_code == 413
        assert (await c.post("/upload", content=b"x" * 1000)).json() == {"n": 1000}
        assert (await c.post("/upload", content=b"x" * 1001)).status_code == 413
        assert (await c.post("/upload/", content=b"x" * 101)).status_code == 413  # exact match only


async def test_app_json_limit_below_upload_limit(monkeypatch):
    from app.core.config import get_settings
    from app.main import JSON_BODY_LIMIT, UPLOAD_PATH, create_app

    monkeypatch.setenv("MAX_UPLOAD_MB", "50")
    get_settings.cache_clear()
    try:
        mw = next(m for m in create_app().user_middleware if m.cls is BodySizeLimitMiddleware)
    finally:
        get_settings.cache_clear()
    assert mw.kwargs["max_bytes"] == JSON_BODY_LIMIT
    assert mw.kwargs["path_limits"] == {UPLOAD_PATH: 50 * 1024 * 1024}


async def test_security_headers():
    async with AsyncClient(transport=ASGITransport(app=_echo_app(1000)), base_url="https://t") as c:
        r = await c.post("/echo", content=b"x")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "strict-transport-security" in r.headers
    assert r.headers["referrer-policy"]
