from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from redis.asyncio import Redis

from app.api.deps import Principal, get_session_principal
from app.core.config import get_settings
from app.core.redis import get_redis
from app.schemas.system import UpdateStatus
from app.services import update_check

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/update-status", response_model=UpdateStatus)
async def update_status(
    principal: Principal = Depends(get_session_principal), redis: Redis = Depends(get_redis)
) -> dict[str, Any]:
    """Is a newer release out? Admin, session only. Reads what the worker stored; never contacts anyone itself."""
    if not principal.user.is_admin:
        raise HTTPException(status_code=403, detail="Administrator required")
    return await update_check.read_status(redis, enabled=get_settings().update_check)
