"""AI chat (Phase 3d). Status and stored conversations; the chat endpoints themselves come in later steps."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.deps import (
    Principal,
    accessible_workspace_ids,
    get_paper_for_user,
    get_principal,
    get_workspace_for_user,
    require_scopes,
)
from app.api.v1.auth import client_ip
from app.core.config import get_settings
from app.core.errors import RateLimitExceeded, not_found
from app.core.ratelimit import RateLimiter, enforce_rate_limit
from app.core.redis import get_redis
from app.core.security import SCOPE_READ_PAPERS, SCOPE_USE_AI, SCOPE_WRITE_PAPERS
from app.db.session import get_db
from app.models.chat import ChatConversation, ChatRole
from app.models.chunk import PaperChunk
from app.models.paper import Paper
from app.schemas.chat import (
    ChatMessageOut,
    ChatRequest,
    ChatStatus,
    ConversationDetail,
    ConversationOut,
    ExplainRegionRequest,
)
from app.services import chat as chat_service
from app.services import llm
from app.services import images
from app.services.chat_prompt import (
    CHUNK_CHARS,
    DEFAULT_REGION_QUESTION,
    Source,
    build_messages,
    build_region_messages,
    clip,
    format_authors,
)
from app.services.chat_stream import (
    ChatRun,
    acquire_slot,
    release_slot,
    seconds_until_utc_midnight,
    stream_chat,
    tokens_used_today,
)

router = APIRouter(prefix="/chat", tags=["chat"])
explain_router = APIRouter(prefix="/papers", tags=["chat"])
logger = logging.getLogger("biblioflow.chat")

CHAT_RATE_LIMIT = 10  # per user and minute
CHAT_IP_RATE_LIMIT = 30  # per client IP and minute
EXPLAIN_RATE_LIMIT = 5  # explain-region, per user and minute (images are costly)
FIRST_EVENT_GRACE = 5.0  # seconds on top of LLM_FIRST_TOKEN_TIMEOUT before the first event counts as missing


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


# ---------------------------------------------------------------- POST /chat (SSE)
def _daily_limit() -> HTTPException:
    return HTTPException(
        status_code=429, detail="daily_limit", headers={"Retry-After": str(seconds_until_utc_midnight())}
    )


@router.post("")
async def chat(
    body: ChatRequest,
    request: Request,
    principal: Principal = Depends(require_scopes(SCOPE_USE_AI)),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Ask one paper (``paper_id``) or the whole library (no ``paper_id``; ``workspace_id`` limits it to one workspace
    you belong to) a question. Answers as ``text/event-stream``: ``meta``, ``delta``*, then ``done`` (or ``error``
    when the model fails after the start). Failures before the start are ordinary HTTP errors."""
    settings = get_settings()
    client = llm.get_client()
    if client is None:
        raise HTTPException(status_code=503, detail="llm_disabled")
    redis = get_redis()
    user_id = principal.user.id
    await enforce_rate_limit(redis, "chat", (str(user_id),), CHAT_RATE_LIMIT)
    await enforce_rate_limit(redis, "chat-ip", (client_ip(request),), CHAT_IP_RATE_LIMIT)

    paper = None
    scope_ws: uuid.UUID | None = None  # library-wide chat: the one workspace searched (None = every readable one)
    if body.paper_id is not None:
        paper = await get_paper_for_user(db, body.paper_id, principal)
    elif body.workspace_id is not None:
        scope_ws = (await get_workspace_for_user(db, body.workspace_id, principal))[0].id  # no access: 404
    conversation: ChatConversation | None = None
    if body.conversation_id is not None:
        conversation = (
            await db.execute(_own_conversations(principal).where(ChatConversation.id == body.conversation_id))
        ).scalar_one_or_none()
        if conversation is None:
            raise not_found()
        if paper is not None and conversation.paper_id != paper.id:
            raise HTTPException(status_code=422, detail="conversation belongs to another paper")
        if paper is None:
            if conversation.paper_id is not None:
                raise HTTPException(status_code=422, detail="conversation belongs to a paper")
            started_in = await chat_service.conversation_scope(db, conversation.id)
            if body.workspace_id is not None and body.workspace_id != started_in:
                raise HTTPException(status_code=422, detail="conversation was started with another workspace")
            if started_in is not None and scope_ws is None:  # re-check: leaving the workspace makes this a 404
                scope_ws = (await get_workspace_for_user(db, started_in, principal))[0].id
    if paper is not None:
        conv_workspace = paper.workspace_id
    elif conversation is not None:
        conv_workspace = conversation.workspace_id
    elif scope_ws is not None:
        conv_workspace = scope_ws
    else:  # unscoped library-wide chat is stored under the user's personal workspace (always readable by them)
        personal = await chat_service.personal_workspace_id(db, user_id)
        if personal is None:
            raise not_found()
        conv_workspace = personal

    async def prepare() -> Prepared:
        history = await chat_service.load_history(db, conversation.id) if conversation else []
        sources, degraded = await chat_service.retrieve_sources(
            db, user_id=user_id, paper_id=paper.id if paper else None, workspace_id=scope_ws,
            message=body.message, limit=settings.llm_max_context_chunks,
        )
        messages = build_messages(body.message, sources, history, cross=paper is None) if sources else None
        return Prepared(messages=messages, sources=sources, degraded=degraded)

    return await _start_stream(
        request, db, principal, client, prepare, conversation=conversation, conv_workspace=conv_workspace,
        paper=paper, user_text=body.message,
        user_meta=None if paper else {"scope_workspace_id": str(scope_ws) if scope_ws else None},
    )


def _text_of(content: Any) -> str:
    """The text of a message ``content`` (a string, or a list of parts of which only text parts count)."""
    if isinstance(content, str):
        return content
    return "".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")


@dataclass
class Prepared:
    """What a request needs to call the model: the messages (None = answer with the fixed reply, no model call)."""

    messages: list[llm.Message] | None
    sources: list[Source]
    degraded: bool = False
    vision: bool = False
    user_meta: dict[str, Any] | None = None  # replaces ``user_meta`` of the request when set


async def _start_stream(
    request: Request,
    db: AsyncSession,
    principal: Principal,
    client: llm.LLMClient,
    prepare: Callable[[], Awaitable[Prepared]],
    *,
    conversation: ChatConversation | None,
    conv_workspace: uuid.UUID,
    paper: Paper | None,
    user_text: str,
    user_meta: dict[str, Any] | None,
) -> StreamingResponse:
    """Shared tail of ``/chat`` and ``/explain-region``: daily budget, concurrency slot, ``prepare()`` (retrieval,
    prompt, image checks), the first event (so a model that is down is a 502 and nothing is saved), then save the
    question and hand the stream over. Access and per-minute limits are checked by the caller."""
    settings = get_settings()
    redis = get_redis()
    user_id = principal.user.id
    # Daily budget: requests are counted by a fixed window (a day), tokens after each answer.
    day = await RateLimiter(
        redis, llm.daily_requests_key(user_id), settings.llm_daily_requests_per_user, 86400
    ).hit()
    if not day.allowed or await tokens_used_today(redis, user_id) >= settings.llm_daily_tokens_per_user:
        raise _daily_limit()
    if not await acquire_slot(redis, user_id):
        raise RateLimitExceeded(5)

    events = first = None
    try:  # from here on the slot belongs to the stream; any early exit gives it back
        prepared = await prepare()
        if prepared.user_meta is not None:
            user_meta = prepared.user_meta
        prompt_chars = 0
        if prepared.messages is not None:
            prompt_chars = sum(len(_text_of(m["content"])) for m in prepared.messages)
            events = client.stream(
                prepared.messages, vision=prepared.vision, max_tokens=settings.llm_max_output_tokens
            )
            try:  # read the first event now, so a model that is down is a 502 and nothing is saved
                first = await asyncio.wait_for(
                    events.__anext__(), settings.llm_first_token_timeout + FIRST_EVENT_GRACE
                )
            except StopAsyncIteration:
                first = None
            except (llm.LLMError, TimeoutError) as exc:
                logger.warning("chat: model unavailable before the start: %s", type(exc).__name__)
                raise HTTPException(status_code=502, detail="llm_unavailable") from None
        if conversation is None:
            conversation = await chat_service.create_conversation(
                db, user_id=user_id, workspace_id=conv_workspace, paper_id=paper.id if paper else None,
                first_message=user_text,
            )
        await chat_service.append_message(
            db, conversation, role=ChatRole.user, content=user_text, meta=user_meta,
        )
        await db.commit()
        run = ChatRun(
            conversation_id=conversation.id, message_id=uuid.uuid4(), user_id=user_id,
            paper_id=paper.id if paper else None, sources=prepared.sources, degraded=prepared.degraded,
            model=(client.vision_model_id if prepared.vision else client.model_id) if prepared.messages is not None else None,
            prompt_chars=prompt_chars, events=events, first=first,
        )
    except BaseException:
        await release_slot(redis, user_id)
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            try:
                await aclose()
            except Exception:  # best effort: the provider stream may already be gone
                pass
        raise
    return StreamingResponse(
        stream_chat(run, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------- POST /papers/{id}/explain-region (SSE)
@explain_router.post("/{paper_id}/explain-region")
async def explain_region(
    paper_id: uuid.UUID,
    body: ExplainRegionRequest,
    request: Request,
    principal: Principal = Depends(require_scopes(SCOPE_USE_AI)),
    db: AsyncSession = Depends(get_db),
) -> StreamingResponse:
    """Explain a rectangle the user cut out of one page (figure, equation, table). Same events as ``POST /chat``;
    ``meta.sources`` is empty and the answer has no citations (the page text is context only). The image is checked
    and re-encoded, sent to the vision model, and never stored or logged."""
    settings = get_settings()
    client = llm.get_client()
    if client is None:
        raise HTTPException(status_code=503, detail="llm_disabled")
    redis = get_redis()
    user_id = principal.user.id
    await enforce_rate_limit(redis, "explain", (str(user_id),), EXPLAIN_RATE_LIMIT)
    await enforce_rate_limit(redis, "chat-ip", (client_ip(request),), CHAT_IP_RATE_LIMIT)

    paper = await get_paper_for_user(db, paper_id, principal)
    r = body.rect
    if paper.page_count is None or body.page_number > paper.page_count:
        raise HTTPException(status_code=422, detail="page_number out of range")
    if not (r.x1 < r.x2 and r.y1 < r.y2 and r.x1 >= 0 and r.y1 >= 0 and r.x2 <= r.width and r.y2 <= r.height):
        raise HTTPException(status_code=422, detail="invalid rect")
    if client.vision_model_id is None:
        raise HTTPException(status_code=501, detail="vision_unsupported")

    conversation: ChatConversation | None = None
    if body.conversation_id is not None:
        conversation = (
            await db.execute(_own_conversations(principal).where(ChatConversation.id == body.conversation_id))
        ).scalar_one_or_none()
        if conversation is None:
            raise not_found()
        if conversation.paper_id != paper.id:
            raise HTTPException(status_code=422, detail="conversation belongs to another paper")

    question = (body.question or "").strip() or DEFAULT_REGION_QUESTION

    async def prepare() -> Prepared:
        try:
            image_url = await images.prepare_image(body.image)
        except images.InvalidImage:
            raise HTTPException(status_code=422, detail="invalid image") from None
        rows = (
            await db.execute(
                select(PaperChunk.section, PaperChunk.text)
                .where(PaperChunk.paper_id == paper.id, PaperChunk.page_number == body.page_number)
                .order_by(PaperChunk.chunk_index)
                .limit(settings.llm_max_context_chunks)
            )
        ).all()
        history = await chat_service.load_history(db, conversation.id) if conversation else []
        messages = build_region_messages(
            question, paper_id=str(paper.id), title=paper.title, authors=format_authors(paper.authors),
            page_number=body.page_number, page_chunks=[(sec, clip(text, CHUNK_CHARS)) for sec, text in rows],
            image_url=image_url, history=history,
        )
        return Prepared(messages=messages, sources=[], vision=True)

    return await _start_stream(
        request, db, principal, client, prepare, conversation=conversation, conv_workspace=paper.workspace_id,
        paper=paper, user_text=question,
        user_meta={"page_number": body.page_number, "rect": r.model_dump()},
    )
