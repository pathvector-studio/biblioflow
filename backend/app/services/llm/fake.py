"""Scripted client for tests (like ``FakeEmbedder``). Never selectable through ``LLM_PROVIDER``."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from app.services.llm.base import Completion, LLMError, Message, StreamEvent, Usage


class FakeLLM:
    def __init__(
        self,
        reply: str = "The paper says so [1].",
        *,
        model_id: str = "fake-model",
        vision: bool = True,
        fail: LLMError | None = None,
        fail_after: int | None = None,
        delay: float = 0.0,
        usage: Usage = Usage(10, 5),
    ) -> None:
        self.reply = reply
        self.model_id = model_id
        self.vision_model_id = "fake-vision" if vision else None
        self.fail = fail  # raised before anything is produced
        self.fail_after = fail_after  # raised after this many deltas
        self.delay = delay  # seconds before each delta
        self.usage = usage
        self.calls: list[dict] = []  # {"messages": [...], "vision": bool, "max_tokens": int | None}

    def _record(self, messages: list[Message], vision: bool, max_tokens: int | None) -> None:
        self.calls.append({"messages": messages, "vision": vision, "max_tokens": max_tokens})

    async def complete(
        self, messages: list[Message], *, vision: bool = False, max_tokens: int | None = None
    ) -> Completion:
        self._record(messages, vision, max_tokens)
        if self.fail:
            raise self.fail
        return Completion(self.reply, self.usage, self.model_id)

    async def stream(
        self, messages: list[Message], *, vision: bool = False, max_tokens: int | None = None
    ) -> AsyncIterator[StreamEvent]:
        self._record(messages, vision, max_tokens)
        if self.fail:
            raise self.fail
        words = self.reply.split(" ")
        for i, word in enumerate(words):
            if self.fail_after is not None and i >= self.fail_after:
                raise LLMError()
            if self.delay:
                await asyncio.sleep(self.delay)
            yield StreamEvent(delta=word + (" " if i < len(words) - 1 else ""))
        yield StreamEvent(usage=self.usage)
