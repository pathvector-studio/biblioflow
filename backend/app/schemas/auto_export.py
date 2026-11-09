import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.models.paper import PaperStatus
from app.services.auto_export import ExportPathError, validate_filename


class AutoExportCreate(BaseModel):
    format: Literal["bibtex", "ris", "csljson"]
    filename: str = Field(min_length=1, max_length=100)
    q: str | None = Field(default=None, max_length=200)
    tag: str | None = Field(default=None, max_length=100)
    collection_id: uuid.UUID | None = None
    year: int | None = Field(default=None, ge=1, le=9999)
    status: PaperStatus | None = None

    @model_validator(mode="after")
    def _check_filename(self) -> "AutoExportCreate":
        try:
            validate_filename(self.filename, self.format)
        except ExportPathError as e:
            raise ValueError(str(e)) from None
        return self


class AutoExportOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    filename: str
    format: str
    path: str  # relative to the export directory, e.g. "<workspace-slug>/refs.bib"
    q: str | None
    tag: str | None
    collection_id: uuid.UUID | None
    year: int | None
    status: str | None
    paper_count: int | None
    last_written_at: datetime | None
    last_error: str | None
    created_at: datetime
