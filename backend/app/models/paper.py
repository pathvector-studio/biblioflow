import enum
import uuid
from typing import Any

from sqlalchemy import BigInteger, Boolean, Computed, ForeignKey, Index, Integer, SmallInteger, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin, pg_enum

PAPER_SEARCH_TSV_EXPR = (
    "to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(abstract,'') || ' ' || coalesce(venue,''))"
)


class IdentifierType(str, enum.Enum):
    arxiv = "arxiv"
    doi = "doi"
    internal = "internal"


class PaperStatus(str, enum.Enum):
    unread = "unread"
    reading = "reading"
    read = "read"
    wishlist = "wishlist"


class IndexStatus(str, enum.Enum):
    pending = "pending"
    processing = "processing"
    ready = "ready"
    failed = "failed"


class Paper(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "papers"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    identifier_type: Mapped[IdentifierType] = mapped_column(
        pg_enum(IdentifierType, "identifier_type"), nullable=False
    )
    doi: Mapped[str | None] = mapped_column(String(255))
    arxiv_id: Mapped[str | None] = mapped_column(String(64))
    citekey: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str] = mapped_column(Text, nullable=False)
    authors: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    year: Mapped[int | None] = mapped_column(SmallInteger)
    venue: Mapped[str | None] = mapped_column(Text)
    abstract: Mapped[str | None] = mapped_column(Text)
    bibtex: Mapped[str | None] = mapped_column(Text)
    status: Mapped[PaperStatus] = mapped_column(
        pg_enum(PaperStatus, "paper_status"), nullable=False, server_default=text("'unread'")
    )
    has_pdf: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    relative_file_path: Mapped[str | None] = mapped_column(Text)
    file_sha256: Mapped[str | None] = mapped_column(String(64))
    file_size: Mapped[int | None] = mapped_column(BigInteger)
    page_count: Mapped[int | None] = mapped_column(Integer)
    index_status: Mapped[IndexStatus] = mapped_column(
        pg_enum(IndexStatus, "index_status"), nullable=False, server_default=text("'pending'")
    )
    notes_md: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    search_tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed(PAPER_SEARCH_TSV_EXPR, persisted=True))

    __table_args__ = (
        UniqueConstraint("workspace_id", "identifier"),
        Index("ix_papers_title_trgm", "title", postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"}),
        Index("ix_papers_search_tsv", "search_tsv", postgresql_using="gin"),
        Index("ix_papers_workspace_status", "workspace_id", "status"),
        Index("ix_papers_workspace_year", "workspace_id", "year"),
    )
