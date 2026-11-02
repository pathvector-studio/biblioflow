"""Password hashing, JWT sessions, personal access tokens and CSRF origin checks."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import get_settings

JWT_ALGORITHM = "HS256"
SESSION_TYP = "session"
PAT_PREFIX = "bf_pat_"
PAT_DISPLAY_PREFIX_LEN = 12
SCOPE_READ_PAPERS = "read:papers"
SCOPE_WRITE_PAPERS = "write:papers"
ALL_SCOPES = (SCOPE_READ_PAPERS, SCOPE_WRITE_PAPERS)
MIN_PASSWORD_LEN = 12
MAX_PASSWORD_LEN = 128

_hasher = PasswordHasher()  # Argon2id by default
# Verified against when the user does not exist, to equalise timing.
_DUMMY_HASH = _hasher.hash("biblioflow-dummy-password")


class TokenError(Exception):
    """Invalid, expired or tampered JWT."""


def validate_password(password: str) -> str:
    if not (MIN_PASSWORD_LEN <= len(password) <= MAX_PASSWORD_LEN):
        raise ValueError(f"password must be {MIN_PASSWORD_LEN}-{MAX_PASSWORD_LEN} characters")
    return password


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    """Constant-ish time check; a missing hash is verified against a dummy."""
    target = password_hash or _DUMMY_HASH
    try:
        ok = _hasher.verify(target, password)
    except (VerificationError, InvalidHashError):
        return False
    return ok and password_hash is not None


def password_needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


# ---------------------------------------------------------------- JWT
@dataclass(frozen=True)
class SessionClaims:
    sub: uuid.UUID
    jti: str
    exp: datetime


def create_access_token(
    user_id: uuid.UUID,
    *,
    secret: str | None = None,
    expires_delta: timedelta | None = None,
) -> tuple[str, str, datetime]:
    """Return ``(token, jti, expires_at)``."""
    settings = get_settings()
    secret = secret or settings.jwt_secret.get_secret_value()
    now = datetime.now(UTC)
    exp = now + (expires_delta if expires_delta is not None else timedelta(minutes=settings.jwt_expire_minutes))
    jti = uuid.uuid4().hex
    payload = {"sub": str(user_id), "jti": jti, "iat": now, "exp": exp, "typ": SESSION_TYP}
    return jwt.encode(payload, secret, algorithm=JWT_ALGORITHM), jti, exp


def decode_access_token(token: str, *, secret: str | None = None) -> SessionClaims:
    secret = secret or get_settings().jwt_secret.get_secret_value()
    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=[JWT_ALGORITHM],
            options={"require": ["exp", "iat", "sub", "jti"]},
        )
        if claims.get("typ") != SESSION_TYP:
            raise TokenError("wrong token type")
        return SessionClaims(
            sub=uuid.UUID(str(claims["sub"])),
            jti=str(claims["jti"]),
            exp=datetime.fromtimestamp(claims["exp"], tz=UTC),
        )
    except (jwt.PyJWTError, ValueError) as exc:
        raise TokenError(str(exc)) from exc


# ---------------------------------------------------------------- PAT
def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_pat() -> tuple[str, str, str]:
    """Return ``(plaintext, display_prefix, sha256_hex)``."""
    token = PAT_PREFIX + secrets.token_urlsafe(32)
    return token, token[:PAT_DISPLAY_PREFIX_LEN], hash_token(token)


# ---------------------------------------------------------------- CSRF
def _normalize_origin(value: str) -> str:
    return value.strip().rstrip("/").lower()


def request_origin_allowed(origin: str | None, referer: str | None, allowed: list[str]) -> bool:
    """True if the Origin (or, failing that, the Referer's origin) is allow-listed."""
    allowed_set = {_normalize_origin(o) for o in allowed}
    if origin is not None:
        return _normalize_origin(origin) in allowed_set
    if referer:
        parts = urlsplit(referer)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}".lower() in allowed_set
    return False
