import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, get_session_principal
from app.core.errors import not_found
from app.core.security import generate_pat
from app.db.session import get_db
from app.models.api_key import ApiKey
from app.schemas.token import TokenCreate, TokenCreated, TokenOut

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.post("", response_model=TokenCreated, status_code=201)
async def create_token(
    body: TokenCreate,
    principal: Principal = Depends(get_session_principal),
    db: AsyncSession = Depends(get_db),
) -> TokenCreated:
    plaintext, prefix, digest = generate_pat()
    expires_at = datetime.now(UTC) + timedelta(days=body.expires_in_days) if body.expires_in_days else None
    key = ApiKey(
        user_id=principal.user.id,
        name=body.name,
        token_prefix=prefix,
        token_hash=digest,
        scopes=list(body.scopes),
        expires_at=expires_at,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)
    return TokenCreated(**TokenOut.model_validate(key).model_dump(), token=plaintext)


@router.get("", response_model=list[TokenOut])
async def list_tokens(
    principal: Principal = Depends(get_session_principal), db: AsyncSession = Depends(get_db)
) -> list[ApiKey]:
    rows = await db.execute(
        select(ApiKey).where(ApiKey.user_id == principal.user.id).order_by(ApiKey.created_at.desc())
    )
    return list(rows.scalars())


@router.delete("/{token_id}", status_code=204)
async def revoke_token(
    token_id: uuid.UUID,
    principal: Principal = Depends(get_session_principal),
    db: AsyncSession = Depends(get_db),
) -> Response:
    key = (
        await db.execute(select(ApiKey).where(ApiKey.id == token_id, ApiKey.user_id == principal.user.id))
    ).scalar_one_or_none()
    if key is None:
        raise not_found()
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        await db.commit()
    return Response(status_code=204)
