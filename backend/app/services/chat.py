"""Chat conversation storage (Phase 3d). The chat endpoints call these; the read side lives in ``api/v1/chat.py``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.chat import ChatConversation, ChatMessage, ChatMessageStatus, ChatRole

TITLE_MAX = 80


def make_title(first_message: str) -> str:
    """First question, whitespace collapsed, cut to 80 characters."""
    return " ".join(first_message.split())[:TITLE_MAX] or "(empty)"


async def create_conversation(
    db: AsyncSession, *, user_id: uuid.UUID, workspace_id: uuid.UUID, paper_id: uuid.UUID | None, first_message: str
) -> ChatConversation:
    """Add a conversation (flushed, not committed). The caller has already checked access to the paper/workspace."""
    conv = ChatConversation(
        user_id=user_id, workspace_id=workspace_id, paper_id=paper_id, title=make_title(first_message)
    )
    db.add(conv)
    await db.flush()
    return conv


async def append_message(
    db: AsyncSession,
    conversation: ChatConversation,
    *,
    role: ChatRole,
    content: str,
    citations: list[dict[str, Any]] | None = None,
    meta: dict[str, Any] | None = None,
    model: str | None = None,
    usage: dict[str, Any] | None = None,
    status: ChatMessageStatus = ChatMessageStatus.complete,
) -> ChatMessage:
    """Add a message and bump the conversation's ``updated_at`` (flushed, not committed)."""
    msg = ChatMessage(
        conversation_id=conversation.id, role=role, content=content, citations=citations or [], meta=meta,
        model=model, usage=usage, status=status,
    )
    db.add(msg)
    conversation.updated_at = func.now()
    await db.flush()
    return msg


async def delete_expired_conversations(
    sessionmaker: async_sessionmaker[AsyncSession], *, retention_days: int, now: datetime | None = None
) -> int:
    """Delete conversations whose last activity is older than ``retention_days`` (0 = keep forever).
    Messages go with them (ON DELETE CASCADE). Returns how many conversations were deleted."""
    if retention_days <= 0:
        return 0
    cutoff = (now or datetime.now(UTC)) - timedelta(days=retention_days)
    async with sessionmaker() as db:
        result = await db.execute(delete(ChatConversation).where(ChatConversation.updated_at < cutoff))
        await db.commit()
        return result.rowcount or 0
