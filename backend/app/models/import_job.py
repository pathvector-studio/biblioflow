import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin

ACTIVE_STATUSES = ("queued", "running")


class ImportJob(UUIDPKMixin, TimestampMixin, Base):
    """An import from the server folder ``{IMPORT_DIR}/{workspace_slug}/`` (bibliography file + PDFs).

    ``updated_at`` doubles as the worker's heartbeat: a job that is queued or running but silent for a while is
    treated as dead. At most one job per workspace is queued or running (partial unique index).
    """

    __tablename__ = "import_jobs"
    __table_args__ = (
        Index(
            "uq_import_jobs_active", "workspace_id", unique=True,
            postgresql_where=text("status IN ('queued', 'running')"),
        ),
        Index("ix_import_jobs_workspace_id_created_at", "workspace_id", "created_at"),
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"))
    source: Mapped[str] = mapped_column(String(300), nullable=False)  # path relative to the workspace folder
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")  # queued|running|done|failed
    total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    new: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unreadable: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    with_attachments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pdfs_processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pdfs_attached: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pdfs_failed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    problems: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
