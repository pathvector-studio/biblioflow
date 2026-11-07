import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ChatStatus(BaseModel):
    enabled: bool
    provider: str  # none | anthropic | openai | gemini | ollama | openai_compatible
    model: str | None  # the configured model name; never a key or base URL
    vision_available: bool
    daily_requests_remaining: int | None  # None when chat is disabled


class ChatMessageOut(BaseModel):
    id: uuid.UUID
    role: str  # user | assistant
    content: str
    citations: list[dict[str, Any]]
    meta: dict[str, Any] | None
    model: str | None
    usage: dict[str, Any] | None
    status: str  # complete | error | cancelled
    created_at: datetime


class ConversationOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    paper_id: uuid.UUID | None  # None = across the library
    title: str
    created_at: datetime
    updated_at: datetime


class ConversationDetail(ConversationOut):
    messages: list[ChatMessageOut]
