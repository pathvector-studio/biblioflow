"""Chat conversation storage (Phase 3d). The chat endpoints call these; the read side lives in ``api/v1/chat.py``."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.chat import ChatConversation, ChatMessage, ChatMessageStatus, ChatRole
from app.services.chat_prompt import Source, clip, format_authors, retrieval_or_query
from app.services.search import run_search

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
    id: uuid.UUID | None = None,
) -> ChatMessage:
    """Add a message and bump the conversation's ``updated_at`` (flushed, not committed)."""
    msg = ChatMessage(
        id=id or uuid.uuid4(), conversation_id=conversation.id, role=role, content=content,
        citations=citations or [], meta=meta, model=model, usage=usage, status=status,
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


async def load_history(db: AsyncSession, conversation_id: uuid.UUID) -> list[tuple[str, str]]:
    """Finished (question, answer) pairs of a conversation, oldest first. A question whose answer failed or was
    cancelled is left out."""
    rows = (
        await db.execute(
            select(ChatMessage.role, ChatMessage.content, ChatMessage.status)
            .where(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at, ChatMessage.id)
        )
    ).all()
    pairs: list[tuple[str, str]] = []
    question: str | None = None
    for role, content, status in rows:
        if role == ChatRole.user:
            question = content
        elif question is not None and status == ChatMessageStatus.complete:
            pairs.append((question, content))
            question = None
        else:
            question = None
    return pairs


async def retrieve_sources(
    db: AsyncSession, *, user_id: uuid.UUID, paper_id: uuid.UUID, message: str, limit: int
) -> tuple[list[Source], bool]:
    """Top ``limit`` chunks of one paper for a question (hybrid; keyword only when semantic search is down, which is
    reported as ``degraded``). Every chunk is cut to ``CHUNK_CHARS``. The caller has checked access to the paper."""
    q = " ".join(message.split())[:500]
    if len(q) < 2:
        return [], False
    outcome = await run_search(db, user_id=user_id, q=q, mode="hybrid", limit=limit, paper_id=paper_id)
    hits = outcome.hits
    if not hits:  # a whole question seldom matches word for word: retry with any of its longer words
        any_word = retrieval_or_query(q)
        if any_word and any_word != q:
            hits = (
                await run_search(db, user_id=user_id, q=any_word, mode="keyword", limit=limit, paper_id=paper_id)
            ).hits
    sources = [
        Source(
            ref=i, chunk_id=str(h.chunk.id), paper_id=str(h.paper.id), title=h.paper.title,
            authors=format_authors(h.paper.authors), page_number=h.chunk.page_number, section=h.chunk.section,
            bboxes=h.chunk.bboxes, text=clip(h.chunk.text),
        )
        for i, h in enumerate(hits[:limit], start=1)
    ]
    return sources, outcome.degraded
