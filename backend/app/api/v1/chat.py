"""AI chat (Phase 3d). Step 1 has only the status endpoint; the chat endpoints come in later steps."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import Principal, get_principal
from app.core.config import get_settings
from app.core.redis import get_redis
from app.schemas.chat import ChatStatus
from app.services import llm

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/status", response_model=ChatStatus)
async def chat_status(principal: Principal = Depends(get_principal)) -> ChatStatus:
    """Is the AI chat on, with which model, and how many requests are left today? Any signed-in user (or token)
    may ask. Keys and base URLs are never returned."""
    settings = get_settings()
    client = llm.get_client()
    if client is None:
        return ChatStatus(
            enabled=False, provider=settings.llm_provider, model=None, vision_available=False, daily_requests_remaining=None
        )
    used = int(await get_redis().get(llm.daily_requests_key(principal.user.id)) or 0)
    return ChatStatus(
        enabled=True,
        provider=settings.llm_provider,
        model=client.model_id,
        vision_available=client.vision_model_id is not None,
        daily_requests_remaining=max(0, settings.llm_daily_requests_per_user - used),
    )
