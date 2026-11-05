import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPKMixin

EMBEDDING_DIM = 1024


class PaperChunk(UUIDPKMixin, CreatedAtMixin, Base):
    __tablename__ = "paper_chunks"

    paper_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    section: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    bboxes: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=sql_text("'[]'::jsonb"))
    token_count: Mapped[int | None] = mapped_column(Integer)
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    # e.g. "bge-m3@5617a9f:int8", "voyage-3.5", "openai:text-embedding-3-large"; null while there is no vector
    embedding_model: Mapped[str | None] = mapped_column(Text)
    tsv: Mapped[Any] = mapped_column(TSVECTOR, Computed("to_tsvector('simple', text)", persisted=True))

    __table_args__ = (
        UniqueConstraint("paper_id", "chunk_index"),
        Index("ix_paper_chunks_tsv", "tsv", postgresql_using="gin"),
        Index("ix_paper_chunks_text_trgm", "text", postgresql_using="gin", postgresql_ops={"text": "gin_trgm_ops"}),
        Index(
            "ix_paper_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
