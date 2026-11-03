from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import DENYLIST_PREFIX, Principal, get_principal, get_session_principal
from app.core.config import SESSION_COOKIE_NAME, get_settings
from app.core.ratelimit import enforce_rate_limit
from app.core.redis import get_redis
from app.core.security import create_access_token, hash_password, password_needs_rehash, verify_password
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, MeResponse, PasswordChangeRequest, RegisterRequest
from app.schemas.user import UserOut
from app.services.users import create_user

router = APIRouter(prefix="/auth", tags=["auth"])

LOGIN_PER_IP_EMAIL = 10
LOGIN_PER_IP = 30


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_session_cookie(response: Response, user: User) -> None:
    settings = get_settings()
    token, _jti, _exp = create_access_token(user.id)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        max_age=settings.jwt_expire_minutes * 60,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> User:
    ip = client_ip(request)
    email = body.email.strip().lower()
    await enforce_rate_limit(redis, "login_ip_email", (ip, email), LOGIN_PER_IP_EMAIL)
    await enforce_rate_limit(redis, "login_ip", (ip,), LOGIN_PER_IP)

    user = (await db.execute(select(User).where(User.email == email))).scalar_one_or_none()
    # Always verify (dummy hash when the user is unknown) to avoid a timing oracle.
    valid = verify_password(body.password, user.password_hash if user else None)
    if not valid or user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid email or password")

    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(body.password)
        await db.commit()

    _set_session_cookie(response, user)
    return user


@router.post("/logout", status_code=204)
async def logout(
    principal: Principal = Depends(get_session_principal),
    redis: Redis = Depends(get_redis),
) -> Response:
    settings = get_settings()
    if principal.jti and principal.session_expires_at:
        ttl = int((principal.session_expires_at - datetime.now(UTC)).total_seconds()) + 1
        if ttl > 0:
            await redis.set(DENYLIST_PREFIX + principal.jti, "1", ex=ttl)
    out = Response(status_code=204)
    out.delete_cookie(
        SESSION_COOKIE_NAME, path="/", httponly=True, secure=settings.cookie_secure, samesite="lax"
    )
    return out


@router.post("/password", status_code=204)
async def change_password(
    body: PasswordChangeRequest,
    principal: Principal = Depends(get_session_principal),
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> Response:
    user = principal.user
    await enforce_rate_limit(redis, "password_change", (str(user.id),), 5)
    if not verify_password(body.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")
    user.password_hash = hash_password(body.new_password)
    user.sessions_valid_after = datetime.now(UTC)
    await db.commit()
    if principal.jti and principal.session_expires_at:
        ttl = int((principal.session_expires_at - datetime.now(UTC)).total_seconds()) + 1
        if ttl > 0:
            await redis.set(DENYLIST_PREFIX + principal.jti, "1", ex=ttl)
    out = Response(status_code=204)
    _set_session_cookie(out, user)
    return out


@router.get("/me", response_model=MeResponse)
async def me(principal: Principal = Depends(get_principal)) -> MeResponse:
    return MeResponse(user=UserOut.model_validate(principal.user), auth_method=principal.auth_method, scopes=sorted(principal.scopes))


@router.post("/register", response_model=UserOut, status_code=201)
async def register(
    body: RegisterRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    redis: Redis = Depends(get_redis),
) -> User:
    if not get_settings().allow_registration:
        raise HTTPException(status_code=403, detail="Registration is disabled")
    await enforce_rate_limit(redis, "register_ip", (client_ip(request),), 10)
    try:
        user = await create_user(db, email=body.email, password=body.password, display_name=body.display_name)
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Email already registered") from None
    return user
