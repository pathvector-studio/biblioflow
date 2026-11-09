"""auto_exports (standing exports written by the worker)

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-08
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "auto_exports",
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column(
            "workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("filename", sa.String(100), nullable=False),
        sa.Column("format", sa.String(16), nullable=False),
        sa.Column("q", sa.String(200), nullable=True),
        sa.Column("tag", sa.String(100), nullable=True),
        sa.Column("collection_id", pg.UUID(as_uuid=True), nullable=True),
        sa.Column("year", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.String(16), nullable=True),
        sa.Column("created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("fingerprint", sa.String(128), nullable=True),
        sa.Column("paper_count", sa.Integer(), nullable=True),
        sa.Column("last_written_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("workspace_id", "filename", name="uq_auto_exports_workspace_id"),
    )


def downgrade() -> None:
    op.drop_table("auto_exports")
