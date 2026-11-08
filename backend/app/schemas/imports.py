from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ImportItemOut(BaseModel):
    ref: str
    title: str
    status: Literal["new", "matched", "unreadable"]
    identifier: str | None = None
    matched_by: Literal["existing", "file"] | None = None  # matched: an existing paper, or an earlier record of the file
    reason: str | None = None  # unreadable: why


class ImportSummary(BaseModel):
    format: Literal["bibtex", "ris", "csljson", "zotero_rdf"]
    dry_run: bool
    total: int
    new: int
    matched: int
    unreadable: int
    with_attachments: int  # records that name a file; the PDFs are not imported from a browser upload
    # Only filled in by the commit (the preview does not write anything).
    tags_added: int = 0
    collections_created: int = 0
    collection_links: int = 0
    notes_added: int = 0
    problems: list[ImportItemOut]  # unreadable records, at most ``MAX_LISTED``
    items: list[ImportItemOut]  # a sample of the new / matched records
    problems_truncated: bool = False
