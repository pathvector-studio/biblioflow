"""paper_chunks.embedding_model: which model produced the vector (vectors of different models are never compared)

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-05
"""
import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("paper_chunks", sa.Column("embedding_model", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("paper_chunks", "embedding_model")
