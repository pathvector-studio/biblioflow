from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.paper import IdentifierType, IndexStatus, PaperStatus
from app.schemas.metadata import Author

MAX_TAGS = 30
MAX_TAG_LEN = 100


class PaperLight(BaseModel):
    id: uuid.UUID
    identifier: str
    citekey: str | None
    title: str
    authors: list[Author]
    year: int | None
    venue: str | None
    tags: list[str]
    status: PaperStatus
    has_pdf: bool
    relative_file_path: str | None


class PaperDetail(PaperLight):
    workspace_id: uuid.UUID
    identifier_type: IdentifierType
    doi: str | None
    arxiv_id: str | None
    abstract: str | None
    bibtex: str | None
    notes_md: str
    collection_ids: list[uuid.UUID]
    file_size: int | None
    page_count: int | None
    index_status: IndexStatus
    created_at: datetime
    updated_at: datetime


def _check_tags(v: list[str] | None) -> list[str] | None:
    if v is None:
        return v
    if len(v) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags")
    for t in v:
        if len(t) > MAX_TAG_LEN:
            raise ValueError(f"tag longer than {MAX_TAG_LEN} characters")
    return v


class PaperPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=1000)
    authors: list[Author] | None = Field(default=None, max_length=500)
    year: int | None = Field(default=None, ge=1, le=9999)
    venue: str | None = Field(default=None, max_length=500)
    abstract: str | None = Field(default=None, max_length=50_000)
    status: PaperStatus | None = None
    citekey: str | None = Field(default=None, min_length=1, max_length=100, pattern=r"^[A-Za-z0-9_:.\-]+$")
    notes_md: str | None = Field(default=None, max_length=500_000)
    tags: list[str] | None = None
    collection_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)

    _tags = field_validator("tags")(_check_tags)

    @model_validator(mode="after")
    def _non_nullable(self) -> PaperPatch:
        nullable = {"year", "venue", "abstract"}
        for name in self.model_fields_set - nullable:
            if getattr(self, name) is None:
                raise ValueError(f"{name} cannot be null")
        return self


class MetadataOnlyIn(BaseModel):
    identifier: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=1000)
    authors: list[Author] = Field(default_factory=list, max_length=500)
    year: int | None = Field(default=None, ge=1, le=9999)
    venue: str | None = Field(default=None, max_length=500)
    workspace_id: uuid.UUID | None = None
    tags: list[str] = Field(default_factory=list)

    _tags = field_validator("tags")(_check_tags)

    @field_validator("identifier", "title")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        v = v.strip() if v is not None else None
        return v or None

    @model_validator(mode="after")
    def _identifier_xor_title(self) -> MetadataOnlyIn:
        if (self.identifier is None) == (self.title is None):
            raise ValueError("provide exactly one of 'identifier' or 'title'")
        return self


class ImportUrlIn(BaseModel):
    url: str = Field(min_length=1, max_length=8192)
    workspace_id: uuid.UUID | None = None
    paper_id: uuid.UUID | None = None
