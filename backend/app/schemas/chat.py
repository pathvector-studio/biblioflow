import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    paper_id: uuid.UUID | None = None  # none = library-wide chat
    workspace_id: uuid.UUID | None = None  # library-wide chat only
    conversation_id: uuid.UUID | None = None

    @field_validator("message")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v


class Rect(BaseModel):
    """A selected region in PDF points (origin bottom-left), with the page size it refers to."""

    model_config = ConfigDict(allow_inf_nan=False)

    x1: float
    y1: float
    x2: float
    y2: float
    width: float = Field(gt=0, le=100_000)
    height: float = Field(gt=0, le=100_000)


class ExplainRegionRequest(BaseModel):
    page_number: int = Field(ge=1, le=100_000)
    rect: Rect
    image: str = Field(min_length=1, max_length=2_200_000)  # data URL; the exact limits are checked after parsing
    question: str | None = Field(default=None, max_length=1000)
    conversation_id: uuid.UUID | None = None


class TranslateRequest(BaseModel):
    """Translate text the user selected in one paper. Nothing of it is stored."""

    text: str = Field(min_length=1, max_length=4000)
    target_lang: str = Field(default="ja", pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,2}$", max_length=20)
    page: int | None = Field(default=None, ge=1, le=100_000)

    @field_validator("text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text must not be blank")
        return v
