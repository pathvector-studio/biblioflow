from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.models.paper import IndexStatus


class ChunkOut(BaseModel):
    id: uuid.UUID
    chunk_index: int
    page_number: int
    section: str | None
    text: str
    bboxes: list[dict[str, Any]]


class ChunksOut(BaseModel):
    paper_id: uuid.UUID
    index_status: IndexStatus
    page_count: int | None
    file_sha256: str | None = None
    page_from: int
    page_to: int
    chunks: list[ChunkOut]


class SearchHit(BaseModel):
    paper_id: uuid.UUID
    workspace_id: uuid.UUID
    title: str
    year: int | None
    citekey: str | None
    chunk_id: uuid.UUID
    page_number: int
    section: str | None
    snippet: str
    bboxes: list[dict[str, Any]]
    score: float
    matched: list[Literal["keyword", "substring", "semantic"]]


class NoteAppendIn(BaseModel):
    text: str = Field(min_length=1, max_length=20_000)


class NoteAppendOut(BaseModel):
    paper_id: uuid.UUID
    notes_length: int


class SearchStatus(BaseModel):
    provider: str  # none | local | voyage | openai
    model_state: str  # disabled | absent | loading | ready | unreachable
    chunks_total: int  # chunks of the papers the caller can see
    chunks_embedded: int  # of those, with a vector from the current model
    degraded: bool  # true: hybrid search currently answers with keyword matches only
