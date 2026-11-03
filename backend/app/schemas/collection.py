import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CollectionOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    parent_id: uuid.UUID | None
    paper_count: int
    created_at: datetime
    updated_at: datetime


class CollectionCreate(BaseModel):
    workspace_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None


class CollectionPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    parent_id: uuid.UUID | None = None


class TagOut(BaseModel):
    id: uuid.UUID
    name: str
    color: str | None
    paper_count: int
