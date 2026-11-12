"""Server-sent events for ``POST /chat``: stream the model's answer, validate it, save it, charge the budget.

The caller (``api/v1/chat.py``) has already checked access, limits and the first token, and holds one slot of the
per-user concurrency limit; this generator releases it. Nothing here logs the question, the answer or excerpt text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import anyio
from redis.asyncio import Redis

from app.core.config import get_settings
from app.core.redis import get_redis
from app.db.session import get_sessionmaker
from app.models.chat import ChatConversation, ChatMessageStatus, ChatRole
from app.services import chat as chat_service
from app.services import llm
from app.services.chat_prompt import NO_RESULT_TEXT, Source, estimate_tokens, strip_images, validate_output
from app.services.llm import LLMError, StreamEvent, Usage

logger = logging.getLogger("biblioflow.chat")

POLL_INTERVAL = 1.0  # seconds between disconnect checks while waiting for the model
PING_INTERVAL = 15.0  # SSE comment so proxies / tunnels do not cut an idle stream (they time out near 100 s)
MAX_CONCURRENT_STREAMS = 2  # per user
SLOT_TTL = 300  # a slot that is never released (crash) frees itself after this many seconds
TOKENS_TTL = 2 * 86400


class DisconnectCheck(Protocol):
    async def is_disconnected(self) -> bool: ...


@dataclass(frozen=True)
class TranslateInfo:
    """A translation run: nothing is saved, and the log line holds only these (never the text)."""

    chars: int
    target_lang: str


@dataclass
class ChatRun:
    conversation_id: uuid.UUID | None  # None for a translation (nothing is saved)
    message_id: uuid.UUID | None  # id of the assistant message, announced in ``meta`` before it exists
    user_id: uuid.UUID
    paper_id: uuid.UUID | None
    sources: list[Source]
    degraded: bool
    model: str | None  # None when no model was called
    prompt_chars: int = 0
    events: AsyncIterator[StreamEvent] | None = None  # None: canned reply, no LLM call
    first: StreamEvent | None = None  # the event already read to prove the model answers
    translate: TranslateInfo | None = None
    _saved: bool = field(default=False, repr=False)

    def meta(self) -> dict[str, Any]:
        if self.translate is not None:
            return {"target_lang": self.translate.target_lang}
        return {
            "conversation_id": str(self.conversation_id), "message_id": str(self.message_id),
            "degraded": self.degraded, "sources": [s.public() for s in self.sources],
        }


class _Disconnected(Exception):
    pass


def sse(event: str, data: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------- Redis budgets
def tokens_key(user_id: object, now: datetime | None = None) -> str:
    return llm.daily_tokens_key(user_id, now)


def seconds_until_utc_midnight(now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    tomorrow = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return max(1, int((tomorrow - now).total_seconds()))


async def tokens_used_today(redis: Redis, user_id: object) -> int:
    return int(await redis.get(tokens_key(user_id)) or 0)


async def charge_tokens(redis: Redis, user_id: object, tokens: int) -> None:
    key = tokens_key(user_id)
    await redis.incrby(key, max(0, tokens))
    await redis.expire(key, TOKENS_TTL)


def _slot_key(user_id: object) -> str:
    return f"bf:ai:streams:{user_id}"


async def acquire_slot(redis: Redis, user_id: object) -> bool:
    """Take one of the user's concurrent-stream slots; False when both are in use."""
    key = _slot_key(user_id)
    count = await redis.incr(key)
    await redis.expire(key, SLOT_TTL)
    if count > MAX_CONCURRENT_STREAMS:
        await release_slot(redis, user_id)
        return False
    return True


async def release_slot(redis: Redis, user_id: object) -> None:
    key = _slot_key(user_id)
    if await redis.decr(key) <= 0:
        await redis.delete(key)


# ---------------------------------------------------------------- the stream
async def _chain(first: StreamEvent | None, rest: AsyncIterator[StreamEvent]) -> AsyncIterator[StreamEvent]:
    if first is not None:
        yield first
    async for event in rest:
        yield event


async def _finalize(
    run: ChatRun, text: str, usage: Usage | None, status: ChatMessageStatus
) -> tuple[str, list[dict[str, Any]], dict[str, int]]:
    """Validate, save the assistant message and charge the token budget. Runs once and shielded from cancellation."""
    if run.translate is not None:  # a translation keeps its [n] markers (citation numbers of the paper)
        clean, citations = strip_images(text), []
    else:
        clean, citations = validate_output(text, run.sources)
    reported = {"input": usage.input, "output": usage.output} if usage and (usage.input or usage.output) else None
    used = reported or {
        "input": estimate_tokens(run.prompt_chars) if run.model else 0,
        "output": estimate_tokens(len(text)) if text and run.model else 0,
    }
    if run._saved:
        return clean, citations, used
    run._saved = True
    with anyio.CancelScope(shield=True):
        if run.translate is None:
            try:
                async with get_sessionmaker()() as db:
                    conv = await db.get(ChatConversation, run.conversation_id)
                    if conv is not None:  # deleted by the user meanwhile: nothing to attach to
                        await chat_service.append_message(
                            db, conv, id=run.message_id, role=ChatRole.assistant, content=clean,
                            citations=citations, model=run.model, usage=used, status=status,
                        )
                        await db.commit()
            except Exception as exc:
                logger.error("saving the chat answer failed: %s", type(exc).__name__)
        if run.model:
            try:
                await charge_tokens(get_redis(), run.user_id, used["input"] + used["output"])
            except Exception as exc:
                logger.warning("charging chat tokens failed: %s", type(exc).__name__)
    return clean, citations, used


async def stream_chat(run: ChatRun, request: DisconnectCheck) -> AsyncIterator[str]:
    """``meta`` -> ``delta``* -> ``done``, or ``error`` after the start. A client that goes away stops the model call
    and the partial answer is saved as ``cancelled``."""
    settings = get_settings()
    started = last_send = time.monotonic()
    parts: list[str] = []
    usage: Usage | None = None
    status = ChatMessageStatus.complete
    error: LLMError | None = None
    chain: AsyncIterator[StreamEvent] | None = None
    pending: asyncio.Future[StreamEvent] | None = None
    try:
        try:
            yield sse("meta", run.meta())
            if run.events is None:
                parts.append(NO_RESULT_TEXT)
                yield sse("delta", {"text": NO_RESULT_TEXT})
            else:
                chain = _chain(run.first, run.events)
                while True:
                    if pending is None:
                        pending = asyncio.ensure_future(chain.__anext__())
                    done, _ = await asyncio.wait({pending}, timeout=POLL_INTERVAL)
                    now = time.monotonic()
                    if not done:
                        if await request.is_disconnected():
                            raise _Disconnected
                        if now - started > settings.llm_timeout:
                            raise LLMError("llm_timeout", "LLM request timed out")
                        if now - last_send >= PING_INTERVAL:
                            last_send = now
                            yield ": ping\n\n"
                        continue
                    task, pending = pending, None
                    try:
                        event = task.result()
                    except StopAsyncIteration:
                        break
                    if event.usage is not None:
                        usage = event.usage
                    if event.delta:
                        parts.append(event.delta)
                        last_send = now
                        yield sse("delta", {"text": event.delta})
        except LLMError as exc:
            status, error = ChatMessageStatus.error, exc
        except _Disconnected:
            status = ChatMessageStatus.cancelled
        except Exception as exc:  # a provider bug must still end in a saved, well-formed ``error``
            logger.error("chat stream failed: %s", type(exc).__name__)
            status, error = ChatMessageStatus.error, LLMError()

        clean, citations, used = await _finalize(run, "".join(parts), usage, status)
        ms = int((time.monotonic() - started) * 1000)
        if run.translate is not None:  # paper id, size, language, time: never the text or the translation
            logger.info(
                "translate status=%s paper=%s chars=%d lang=%s in=%d out=%d ms=%d", status.value, run.paper_id,
                run.translate.chars, run.translate.target_lang, used["input"], used["output"], ms,
            )
        else:
            logger.info(
                "chat status=%s sources=%d model=%s in=%d out=%d ms=%d",
                status.value, len(run.sources), run.model, used["input"], used["output"], ms,
            )
        if status == ChatMessageStatus.complete and run.translate is not None:
            yield sse("done", {"content": clean, "usage": used})
        elif status == ChatMessageStatus.complete:
            yield sse(
                "done", {"message_id": str(run.message_id), "content": clean, "usage": used, "citations": citations}
            )
        elif error is not None:
            yield sse("error", {"code": error.code, "message": error.message})
    except (asyncio.CancelledError, GeneratorExit):
        await _finalize(run, "".join(parts), usage, ChatMessageStatus.cancelled)
        raise
    finally:
        with anyio.CancelScope(shield=True):
            if pending is not None:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
            for it in (chain, run.events):
                aclose = getattr(it, "aclose", None)
                if aclose is not None:
                    try:
                        await aclose()
                    except Exception:  # closing a half-read provider stream may fail; nothing to recover
                        pass
            try:
                await release_slot(get_redis(), run.user_id)
            except Exception as exc:
                logger.warning("releasing the chat slot failed: %s", type(exc).__name__)
