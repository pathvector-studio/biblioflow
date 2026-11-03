"""workspace_invitations (e-mail keyed, accept-to-join)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-04
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "workspace_invitations",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column(
            "workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("email", pg.CITEXT(), nullable=False),
        sa.Column(
            "role",
            pg.ENUM("owner", "editor", "viewer", name="workspace_role", create_type=False),
            nullable=False,
        ),
        sa.Column("invited_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("workspace_id", "email", name="uq_workspace_invitations_workspace_id"),
    )
    op.create_index("ix_workspace_invitations_email", "workspace_invitations", ["email"])


def downgrade() -> None:
    op.drop_table("workspace_invitations")
