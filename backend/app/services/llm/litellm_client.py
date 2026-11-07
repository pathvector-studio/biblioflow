"""LiteLLM-backed client (SDK only: no proxy, no telemetry, no callbacks)."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

from app.core.config import Settings
from app.services.llm.base import Completion, LLMError, Message, StreamEvent, Usage

logger = logging.getLogger("biblioflow.llm")

_PREFIX = {
    "anthropic": "anthropic",
    "openai": "openai",
    "gemini": "gemini",
    "ollama": "ollama_chat",
    "openai_compatible": "openai",
}


def litellm_model(provider: str, model: str) -> str:
    """The LiteLLM model string for a provider (``anthropic/claude-...``). An explicit prefix is kept."""
    prefix = _PREFIX[provider]
    return model if model.startswith(f"{prefix}/") else f"{prefix}/{model}"


def _load_litellm() -> Any:
    import litellm

    litellm.telemetry = False
    litellm.success_callback = []
    litellm.failure_callback = []
    litellm.suppress_debug_info = True
    return litellm


def _usage(raw: Any) -> Usage:
    if raw is None:
        return Usage()
    return Usage(int(getattr(raw, "prompt_tokens", 0) or 0), int(getattr(raw, "completion_tokens", 0) or 0))


class LiteLLMClient:
    def __init__(self, settings: Settings) -> None:
        assert settings.llm_model  # validated by Settings
        self._key = settings.llm_api_key.get_secret_value() if settings.llm_api_key else None
        self._base = settings.llm_api_base or None
        self._timeout = settings.llm_timeout
        self._first_token_timeout = settings.llm_first_token_timeout
        self._max_tokens = settings.llm_max_output_tokens
        self._chat_model = litellm_model(settings.llm_provider, settings.llm_model)
        self._vision_model = (
            litellm_model(settings.llm_provider, settings.vision_model) if settings.vision_model else None
        )
        self.model_id = settings.llm_model

    @property
    def vision_model_id(self) -> str | None:
        if self._vision_model:
            return self._vision_model
        try:
            return self._chat_model if _load_litellm().supports_vision(self._chat_model) else None
        except Exception:  # unknown model string
            return None

    def _kwargs(self, messages: list[Message], vision: bool, max_tokens: int | None) -> dict[str, Any]:
        kw: dict[str, Any] = {
            "model": self._vision_model if vision and self._vision_model else self._chat_model,
            "messages": messages,
            "max_tokens": min(max_tokens or self._max_tokens, self._max_tokens),
            "timeout": self._timeout,
            "num_retries": 0,
        }
        if self._key:
            kw["api_key"] = self._key
        if self._base:
            kw["api_base"] = self._base
        return kw

    @staticmethod
    def _fail(exc: BaseException) -> LLMError:
        timeout = isinstance(exc, TimeoutError) or type(exc).__name__ in {"Timeout", "APITimeoutError"}
        logger.warning("llm call failed: %s", type(exc).__name__)  # type only: messages can hold keys
        return LLMError("llm_timeout", "LLM request timed out") if timeout else LLMError()

    async def complete(
        self, messages: list[Message], *, vision: bool = False, max_tokens: int | None = None
    ) -> Completion:
        litellm = _load_litellm()
        kw = self._kwargs(messages, vision, max_tokens)
        started = time.monotonic()
        try:
            resp = await litellm.acompletion(**kw)
            text = resp.choices[0].message.content or ""
            usage = _usage(getattr(resp, "usage", None))
        except Exception as exc:
            raise self._fail(exc) from None
        ms = int((time.monotonic() - started) * 1000)
        logger.info("llm complete model=%s in=%d out=%d ms=%d", kw["model"], usage.input, usage.output, ms)
        return Completion(text, usage, self.model_id)

    async def stream(
        self, messages: list[Message], *, vision: bool = False, max_tokens: int | None = None
    ) -> AsyncIterator[StreamEvent]:
        litellm = _load_litellm()
        kw = self._kwargs(messages, vision, max_tokens)
        kw["stream"] = True
        kw["stream_options"] = {"include_usage": True}
        started = time.monotonic()
        usage = Usage()
        try:
            resp = await asyncio.wait_for(litellm.acompletion(**kw), self._first_token_timeout)
            it = resp.__aiter__()
            first = True
            while True:
                try:
                    chunk = await (
                        asyncio.wait_for(it.__anext__(), self._first_token_timeout) if first else it.__anext__()
                    )
                except StopAsyncIteration:
                    break
                first = False
                if getattr(chunk, "usage", None):
                    usage = _usage(chunk.usage)
                choices = getattr(chunk, "choices", None) or []
                text = (choices[0].delta.content if choices else None) or ""
                if text:
                    yield StreamEvent(delta=text)
        except Exception as exc:
            raise self._fail(exc) from None
        ms = int((time.monotonic() - started) * 1000)
        logger.info("llm stream model=%s in=%d out=%d ms=%d", kw["model"], usage.input, usage.output, ms)
        yield StreamEvent(usage=usage)
