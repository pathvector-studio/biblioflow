"""trigram index on paper_chunks.text (substring search for scripts without word boundaries)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-05
"""
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_paper_chunks_text_trgm",
        "paper_chunks",
        ["text"],
        postgresql_using="gin",
        postgresql_ops={"text": "gin_trgm_ops"},
    )


def downgrade() -> None:
    op.drop_index("ix_paper_chunks_text_trgm", table_name="paper_chunks")
