"""Authentication principal resolution and workspace/paper access checks (IDOR-safe)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from fastapi import Depends, HTTPException, Request
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Select

from app.core.config import SESSION_COOKIE_NAME, get_settings
from app.core.errors import not_found
from app.core.redis import get_redis
from app.core.security import (
    ALL_SCOPES,
    PAT_PREFIX,
    TokenError,
    decode_access_token,
    hash_token,
    request_origin_allowed,
)
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.models.paper import Paper
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceMember, WorkspaceRole

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
LAST_USED_INTERVAL = timedelta(seconds=60)
DENYLIST_PREFIX = "bf:jwt:deny:"


@dataclass(frozen=True)
class Principal:
    user: User
    auth_method: Literal["session", "pat"]
    scopes: frozenset[str]
    jti: str | None = None
    session_expires_at: datetime | None = None


def _unauthorized(detail: str = "Not authenticated") -> HTTPException:
    return HTTPException(status_code=401, detail=detail, headers={"WWW-Authenticate": "Bearer"})


async def _principal_from_pat(token: str, db: AsyncSession) -> Principal:
    row = (
        await db.execute(
            select(ApiKey, User).join(User, User.id == ApiKey.user_id).where(ApiKey.token_hash == hash_token(token))
        )
    ).first()
    now = datetime.now(UTC)
    if row is None:
        raise _unauthorized("Invalid token")
    key, user = row
    if key.revoked_at is not None or (key.expires_at is not None and key.expires_at <= now) or not user.is_active:
        raise _unauthorized("Invalid token")
    if key.last_used_at is None or now - key.last_used_at >= LAST_USED_INTERVAL:
        await db.execute(update(ApiKey).where(ApiKey.id == key.id).values(last_used_at=now))
        await db.commit()
    return Principal(user=user, auth_method="pat", scopes=frozenset(key.scopes))


async def _principal_from_cookie(token: str, db: AsyncSession, redis: Redis) -> Principal:
    try:
        claims = decode_access_token(token)
    except TokenError:
        raise _unauthorized("Invalid session") from None
    if await redis.exists(DENYLIST_PREFIX + claims.jti):
        raise _unauthorized("Session revoked")
    user = await db.get(User, claims.sub)
    if user is None or not user.is_active:
        raise _unauthorized("Invalid session")
    return Principal(
        user=user,
        auth_method="session",
        scopes=frozenset(ALL_SCOPES),
        jti=claims.jti,
        session_expires_at=claims.exp,
    )


def enforce_csrf(request: Request) -> None:
    if request.method not in UNSAFE_METHODS:
        return
    allowed = get_settings().cors_allowed_origins
    if not request_origin_allowed(request.headers.get("origin"), request.headers.get("referer"), allowed):
        raise HTTPException(status_code=403, detail="Origin not allowed")


async def get_principal(
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Principal:
    auth = request.headers.get("authorization")
    if auth:
        scheme, _, token = auth.partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token.startswith(PAT_PREFIX):
            raise _unauthorized("Invalid token")
        return await _principal_from_pat(token, db)  # Bearer tokens are CSRF-exempt

    cookie = request.cookies.get(SESSION_COOKIE_NAME)
    if not cookie:
        raise _unauthorized()
    principal = await _principal_from_cookie(cookie, db, redis)
    enforce_csrf(request)
    return principal


async def get_session_principal(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.auth_method != "session":
        raise HTTPException(status_code=403, detail="Session authentication required")
    return principal


def require_scopes(*required: str) -> Callable[..., Principal]:
    async def _dep(principal: Principal = Depends(get_principal)) -> Principal:
        missing = [s for s in required if s not in principal.scopes]
        if missing:
            raise HTTPException(status_code=403, detail=f"Missing scope: {', '.join(missing)}")
        return principal

    return _dep


# ---------------------------------------------------------------- RBAC
def accessible_workspace_ids(user_id: uuid.UUID) -> Select:
    """Subquery of workspace ids the user is a member of; use in every list query."""
    return select(WorkspaceMember.workspace_id).where(WorkspaceMember.user_id == user_id)


async def get_workspace_for_user(
    db: AsyncSession,
    workspace_id: uuid.UUID,
    principal: Principal,
    min_role: WorkspaceRole = WorkspaceRole.viewer,
) -> tuple[Workspace, WorkspaceRole]:
    """Single membership-joined query; 404 if missing or role insufficient."""
    row = (
        await db.execute(
            select(Workspace, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .where(Workspace.id == workspace_id, WorkspaceMember.user_id == principal.user.id)
        )
    ).first()
    if row is None or not row[1].at_least(min_role):
        raise not_found()
    return row[0], row[1]


async def get_paper_for_user(
    db: AsyncSession,
    paper_id: uuid.UUID,
    principal: Principal,
    min_role: WorkspaceRole = WorkspaceRole.viewer,
) -> Paper:
    row = (
        await db.execute(
            select(Paper, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Paper.workspace_id)
            .where(Paper.id == paper_id, WorkspaceMember.user_id == principal.user.id)
        )
    ).first()
    if row is None or not row[1].at_least(min_role):
        raise not_found()
    return row[0]
