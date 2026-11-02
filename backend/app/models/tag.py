import uuid

from sqlalchemy import Column, ForeignKey, String, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPKMixin


class Tag(UUIDPKMixin, CreatedAtMixin, Base):
    __tablename__ = "tags"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    color: Mapped[str | None] = mapped_column(String(16))

    __table_args__ = (UniqueConstraint("workspace_id", "name"),)


paper_tags = Table(
    "paper_tags",
    Base.metadata,
    Column("paper_id", UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", UUID(as_uuid=True), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True, index=True),
)
