"""AI chat (Phase 3d). Status and stored conversations; the chat endpoints themselves come in later steps."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import Principal, accessible_workspace_ids, get_paper_for_user, get_principal, require_scopes
from app.core.config import get_settings
from app.core.errors import not_found
from app.core.redis import get_redis
from app.core.security import SCOPE_READ_PAPERS, SCOPE_WRITE_PAPERS
from app.db.session import get_db
from app.models.chat import ChatConversation
from app.schemas.chat import ChatMessageOut, ChatStatus, ConversationDetail, ConversationOut
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


def _own_conversations(principal: Principal):
    """Conversations the caller may see right now: theirs, in a workspace they are still a member of (a paper
    belongs to its conversation's workspace, so losing the workspace also hides paper conversations)."""
    return select(ChatConversation).where(
        ChatConversation.user_id == principal.user.id,
        ChatConversation.workspace_id.in_(accessible_workspace_ids(principal.user.id)),
    )


def _out(c: ChatConversation) -> ConversationOut:
    return ConversationOut(
        id=c.id, workspace_id=c.workspace_id, paper_id=c.paper_id, title=c.title,
        created_at=c.created_at, updated_at=c.updated_at,
    )


@router.get("/conversations", response_model=list[ConversationOut])
async def list_conversations(
    paper_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> list[ConversationOut]:
    """Your own conversations, newest activity first; ``paper_id`` limits them to one paper (404 if you cannot see it)."""
    q = _own_conversations(principal)
    if paper_id is not None:
        await get_paper_for_user(db, paper_id, principal)
        q = q.where(ChatConversation.paper_id == paper_id)
    rows = (
        await db.execute(q.order_by(ChatConversation.updated_at.desc(), ChatConversation.id).limit(limit).offset(offset))
    ).scalars().all()
    return [_out(c) for c in rows]


async def _get_own(db: AsyncSession, conversation_id: uuid.UUID, principal: Principal) -> ChatConversation:
    conv = (
        await db.execute(
            _own_conversations(principal)
            .where(ChatConversation.id == conversation_id)
            .options(selectinload(ChatConversation.messages))
        )
    ).scalar_one_or_none()
    if conv is None:
        raise not_found()
    return conv


@router.get("/conversations/{conversation_id}", response_model=ConversationDetail)
async def get_conversation(
    conversation_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> ConversationDetail:
    conv = await _get_own(db, conversation_id, principal)
    return ConversationDetail(
        **_out(conv).model_dump(),
        messages=[
            ChatMessageOut(
                id=m.id, role=m.role.value, content=m.content, citations=m.citations, meta=m.meta, model=m.model,
                usage=m.usage, status=m.status.value, created_at=m.created_at,
            )
            for m in conv.messages
        ],
    )


@router.delete("/conversations/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    conv = await _get_own(db, conversation_id, principal)
    await db.execute(delete(ChatConversation).where(ChatConversation.id == conv.id))
    await db.commit()
    return Response(status_code=204)
