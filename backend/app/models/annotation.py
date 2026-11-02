import enum
import uuid
from typing import Any

from sqlalchemy import ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, TimestampMixin, UUIDPKMixin, pg_enum


class AnnotationKind(str, enum.Enum):
    highlight = "highlight"
    area = "area"
    note = "note"


class Annotation(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "annotations"

    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[AnnotationKind] = mapped_column(pg_enum(AnnotationKind, "annotation_kind"), nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    content_text: Mapped[str | None] = mapped_column(Text)
    comment: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'#ffe066'"))
