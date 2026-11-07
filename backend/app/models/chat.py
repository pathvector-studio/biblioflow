import enum
import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, Text, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPKMixin, pg_enum


class ChatRole(str, enum.Enum):
    user = "user"
    assistant = "assistant"


class ChatMessageStatus(str, enum.Enum):
    complete = "complete"
    error = "error"
    cancelled = "cancelled"


class ChatConversation(UUIDPKMixin, TimestampMixin, Base):
    """A conversation is private to its creator, even inside a shared workspace."""

    __tablename__ = "chat_conversations"
    __table_args__ = (Index("ix_chat_conversations_user_id_updated_at", "user_id", "updated_at"),)

    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False, index=True
    )
    paper_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("papers.id", ondelete="CASCADE"), nullable=True, index=True
    )  # null = across the library
    title: Mapped[str] = mapped_column(Text, nullable=False)

    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation", order_by="ChatMessage.created_at, ChatMessage.id", passive_deletes=True
    )


class ChatMessage(UUIDPKMixin, CreatedAtMixin, Base):
    __tablename__ = "chat_messages"

    conversation_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("chat_conversations.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[ChatRole] = mapped_column(pg_enum(ChatRole, "chat_message_role"), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # explain-region: {"page_number", "rect"}
    model: Mapped[str | None] = mapped_column(Text)
    usage: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # {"input": n, "output": n}
    status: Mapped[ChatMessageStatus] = mapped_column(
        pg_enum(ChatMessageStatus, "chat_message_status"), nullable=False, server_default=text("'complete'")
    )

    conversation: Mapped[ChatConversation] = relationship(back_populates="messages")
