"""users.sessions_valid_after (session invalidation on password change)

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-03
"""
import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("sessions_valid_after", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "sessions_valid_after")
