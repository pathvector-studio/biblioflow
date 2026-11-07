"""chat_conversations / chat_messages (Phase 3d)

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-07
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

ROLE = ("user", "assistant")
STATUS = ("complete", "error", "cancelled")


def upgrade() -> None:
    bind = op.get_bind()
    pg.ENUM(*ROLE, name="chat_message_role").create(bind, checkfirst=True)
    pg.ENUM(*STATUS, name="chat_message_status").create(bind, checkfirst=True)

    op.create_table(
        "chat_conversations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("paper_id", pg.UUID(as_uuid=True), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=True),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_conversations_user_id_updated_at", "chat_conversations", ["user_id", "updated_at"])
    op.create_index("ix_chat_conversations_workspace_id", "chat_conversations", ["workspace_id"])
    op.create_index("ix_chat_conversations_paper_id", "chat_conversations", ["paper_id"])

    op.create_table(
        "chat_messages",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "conversation_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("chat_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("role", pg.ENUM(*ROLE, name="chat_message_role", create_type=False), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("citations", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("meta", pg.JSONB(), nullable=True),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("usage", pg.JSONB(), nullable=True),
        sa.Column(
            "status",
            pg.ENUM(*STATUS, name="chat_message_status", create_type=False),
            nullable=False,
            server_default=sa.text("'complete'"),
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_chat_messages_conversation_id", "chat_messages", ["conversation_id"])


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chat_conversations")
    bind = op.get_bind()
    pg.ENUM(*STATUS, name="chat_message_status").drop(bind, checkfirst=True)
    pg.ENUM(*ROLE, name="chat_message_role").drop(bind, checkfirst=True)
