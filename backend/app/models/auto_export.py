import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, SmallInteger, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin


class AutoExport(UUIDPKMixin, TimestampMixin, Base):
    """A standing export: the worker keeps ``{EXPORT_DIR}/{workspace_slug}/{filename}`` up to date."""

    __tablename__ = "auto_exports"
    __table_args__ = (UniqueConstraint("workspace_id", "filename"),)

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(100), nullable=False)
    format: Mapped[str] = mapped_column(String(16), nullable=False)  # bibtex | ris | csljson
    # Filters, the same as GET /papers/light. collection_id is deliberately not a foreign key: a deleted
    # collection leaves an empty export instead of silently widening it to the whole library.
    q: Mapped[str | None] = mapped_column(String(200))
    tag: Mapped[str | None] = mapped_column(String(100))
    collection_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    year: Mapped[int | None] = mapped_column(SmallInteger)
    status: Mapped[str | None] = mapped_column(String(16))
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    # Change detection: fingerprint of the matching papers at the last write (count, newest update, hash of ids).
    fingerprint: Mapped[str | None] = mapped_column(String(128))
    paper_count: Mapped[int | None] = mapped_column(Integer)
    last_written_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
