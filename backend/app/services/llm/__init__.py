"""Chat / vision models behind one interface. ``get_client()`` returns ``None`` for ``LLM_PROVIDER=none``.

The API layer imports only this package (never LiteLLM). Call ``llm.get_client()`` through the module so tests
can replace it with a ``FakeLLM``.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.core.config import Settings, get_settings
from app.services.llm.base import Completion, LLMClient, LLMError, Message, StreamEvent, Usage
from app.services.llm.fake import FakeLLM
from app.services.llm.litellm_client import LiteLLMClient

__all__ = [
    "Completion",
    "FakeLLM",
    "LLMClient",
    "LLMError",
    "Message",
    "StreamEvent",
    "Usage",
    "build_client",
    "daily_requests_key",
    "get_client",
]


def build_client(settings: Settings) -> LLMClient | None:
    if settings.llm_provider == "none":
        return None
    return LiteLLMClient(settings)


def get_client() -> LLMClient | None:
    """The configured client, or ``None`` when chat is switched off."""
    return build_client(get_settings())


def daily_requests_key(user_id: object, now: datetime | None = None) -> str:
    """Redis key of the per-user daily request counter (a ``RateLimiter`` with window 86400 writes it)."""
    day = (now or datetime.now(UTC)).strftime("%Y-%m-%d")
    return f"bf:ai:day:{user_id}:{day}"
