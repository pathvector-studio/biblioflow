"""unique citekey per workspace

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-03
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_unique_constraint("uq_papers_workspace_id_citekey", "papers", ["workspace_id", "citekey"])


def downgrade() -> None:
    op.drop_constraint("uq_papers_workspace_id_citekey", "papers", type_="unique")
