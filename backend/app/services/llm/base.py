"""The interface the API layer knows. Nothing outside ``app.services.llm`` imports LiteLLM."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Literal, Protocol

# OpenAI-style chat messages; a ``user`` content may be a list with ``image_url`` parts (explain-region).
Message = dict[str, Any]


class LLMError(Exception):
    """Any failure of the provider. The message is fixed text: LiteLLM's own messages can contain keys or URLs."""

    def __init__(self, code: Literal["llm_timeout", "llm_error"] = "llm_error", message: str = "LLM request failed") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Usage:
    input: int = 0
    output: int = 0


@dataclass(frozen=True)
class Completion:
    text: str
    usage: Usage
    model: str


@dataclass(frozen=True)
class StreamEvent:
    """``delta`` carries text; the last event of a stream has ``usage`` set (and an empty ``delta``)."""

    delta: str = ""
    usage: Usage | None = None


class LLMClient(Protocol):
    model_id: str  # shown to users (``/chat/status``) and stored with each message; never contains secrets

    @property
    def vision_model_id(self) -> str | None:
        """None: images are not supported."""
        ...

    async def complete(self, messages: list[Message], *, vision: bool = False, max_tokens: int | None = None) -> Completion: ...

    def stream(
        self, messages: list[Message], *, vision: bool = False, max_tokens: int | None = None
    ) -> AsyncIterator[StreamEvent]: ...
