import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ImportFolderFile(BaseModel):
    name: str
    size: int
    format: Literal["bibtex", "ris", "csljson", "zotero_rdf"]


class ImportFolderOut(BaseModel):
    slug: str  # the folder is data/import/<slug>/ on the server
    exists: bool
    files: list[ImportFolderFile]


class ImportJobCreate(BaseModel):
    source: str = Field(min_length=1, max_length=300, description="A name from the folder listing")


class ImportJobProblem(BaseModel):
    ref: str
    reason: str
    stage: Literal["record", "pdf"]


class ImportJobOut(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    source: str
    status: Literal["queued", "running", "done", "failed"]
    total: int
    new: int
    matched: int
    unreadable: int
    with_attachments: int  # records that have a file attachment
    pdfs_processed: int  # ... of which handled so far
    pdfs_attached: int
    pdfs_failed: int
    problems: list[ImportJobProblem]
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
