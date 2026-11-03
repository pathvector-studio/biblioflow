import json
import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.models.annotation import AnnotationKind

MAX_POSITION_BYTES = 64 * 1024
COLOR_PATTERN = r"^#[0-9a-fA-F]{6}$"


def _check_position(v: dict[str, Any]) -> dict[str, Any]:
    if len(json.dumps(v, separators=(",", ":")).encode("utf-8")) > MAX_POSITION_BYTES:
        raise ValueError("position must be at most 64KB")
    return v


class AnnotationOut(BaseModel):
    id: uuid.UUID
    paper_id: uuid.UUID
    user_id: uuid.UUID
    user_display_name: str
    kind: AnnotationKind
    page_number: int
    position: dict[str, Any]
    content_text: str | None
    comment: str | None
    color: str
    created_at: datetime
    updated_at: datetime


class AnnotationCreate(BaseModel):
    kind: AnnotationKind
    page_number: int = Field(ge=1)
    position: dict[str, Any]
    content_text: str | None = Field(default=None, max_length=20000)
    comment: str | None = Field(default=None, max_length=20000)
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)

    @field_validator("position")
    @classmethod
    def _pos(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _check_position(v)


class AnnotationPatch(BaseModel):
    comment: str | None = Field(default=None, max_length=20000)
    color: str | None = Field(default=None, pattern=COLOR_PATTERN)
    position: dict[str, Any] | None = None

    @field_validator("position")
    @classmethod
    def _pos(cls, v: dict[str, Any] | None) -> dict[str, Any] | None:
        return v if v is None else _check_position(v)
