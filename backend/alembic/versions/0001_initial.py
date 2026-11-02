"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-11-03
"""
import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ENUMS = {
    "workspace_kind": ("personal", "shared"),
    "workspace_role": ("owner", "editor", "viewer"),
    "identifier_type": ("arxiv", "doi", "internal"),
    "paper_status": ("unread", "reading", "read", "wishlist"),
    "index_status": ("pending", "processing", "ready", "failed"),
    "annotation_kind": ("highlight", "area", "note"),
}

PAPER_TSV = "to_tsvector('simple', coalesce(title,'') || ' ' || coalesce(abstract,'') || ' ' || coalesce(venue,''))"


def enum(name: str) -> pg.ENUM:
    return pg.ENUM(*ENUMS[name], name=name, create_type=False)


def uuid_pk() -> sa.Column:
    return sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()"))


def created_at() -> sa.Column:
    return sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def updated_at() -> sa.Column:
    return sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)


def upgrade() -> None:
    for ext in ("vector", "pg_trgm", "citext"):
        op.execute(f"CREATE EXTENSION IF NOT EXISTS {ext}")
    bind = op.get_bind()
    for name, values in ENUMS.items():
        pg.ENUM(*values, name=name).create(bind, checkfirst=True)

    op.create_table(
        "users",
        uuid_pk(),
        sa.Column("email", pg.CITEXT(), nullable=False),
        sa.Column("password_hash", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        created_at(),
        updated_at(),
        sa.UniqueConstraint("email"),
    )

    op.create_table(
        "api_keys",
        uuid_pk(),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("token_prefix", sa.String(16), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("scopes", pg.ARRAY(sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        created_at(),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])

    op.create_table(
        "workspaces",
        uuid_pk(),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("kind", enum("workspace_kind"), nullable=False),
        sa.Column("owner_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        created_at(),
        updated_at(),
        sa.UniqueConstraint("slug"),
    )
    op.create_index("ix_workspaces_owner_id", "workspaces", ["owner_id"])

    op.create_table(
        "workspace_members",
        sa.Column(
            "workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
        ),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("role", enum("workspace_role"), nullable=False),
        created_at(),
    )
    op.create_index("ix_workspace_members_user_id", "workspace_members", ["user_id"])

    op.create_table(
        "papers",
        uuid_pk(),
        sa.Column(
            "workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("created_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("identifier", sa.String(255), nullable=False),
        sa.Column("identifier_type", enum("identifier_type"), nullable=False),
        sa.Column("doi", sa.String(255)),
        sa.Column("arxiv_id", sa.String(64)),
        sa.Column("citekey", sa.String(255)),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("authors", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("year", sa.SmallInteger()),
        sa.Column("venue", sa.Text()),
        sa.Column("abstract", sa.Text()),
        sa.Column("bibtex", sa.Text()),
        sa.Column("status", enum("paper_status"), nullable=False, server_default=sa.text("'unread'")),
        sa.Column("has_pdf", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("relative_file_path", sa.Text()),
        sa.Column("file_sha256", sa.String(64)),
        sa.Column("file_size", sa.BigInteger()),
        sa.Column("page_count", sa.Integer()),
        sa.Column("index_status", enum("index_status"), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("notes_md", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("search_tsv", pg.TSVECTOR(), sa.Computed(PAPER_TSV, persisted=True)),
        created_at(),
        updated_at(),
        sa.UniqueConstraint("workspace_id", "identifier"),
    )
    op.create_index("ix_papers_title_trgm", "papers", ["title"], postgresql_using="gin", postgresql_ops={"title": "gin_trgm_ops"})
    op.create_index("ix_papers_search_tsv", "papers", ["search_tsv"], postgresql_using="gin")
    op.create_index("ix_papers_workspace_status", "papers", ["workspace_id", "status"])
    op.create_index("ix_papers_workspace_year", "papers", ["workspace_id", "year"])

    op.create_table(
        "paper_chunks",
        uuid_pk(),
        sa.Column("paper_id", pg.UUID(as_uuid=True), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("section", sa.Text()),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("bboxes", pg.JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("token_count", sa.Integer()),
        sa.Column("embedding", Vector(1024), nullable=True),
        sa.Column("tsv", pg.TSVECTOR(), sa.Computed("to_tsvector('simple', text)", persisted=True)),
        created_at(),
        sa.UniqueConstraint("paper_id", "chunk_index"),
    )
    op.create_index("ix_paper_chunks_tsv", "paper_chunks", ["tsv"], postgresql_using="gin")
    op.create_index(
        "ix_paper_chunks_embedding_hnsw",
        "paper_chunks",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )

    op.create_table(
        "annotations",
        uuid_pk(),
        sa.Column("paper_id", pg.UUID(as_uuid=True), sa.ForeignKey("papers.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_id", pg.UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", enum("annotation_kind"), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("position", pg.JSONB(), nullable=False),
        sa.Column("content_text", sa.Text()),
        sa.Column("comment", sa.Text()),
        sa.Column("color", sa.String(16), nullable=False, server_default=sa.text("'#ffe066'")),
        created_at(),
        updated_at(),
    )
    op.create_index("ix_annotations_paper_id", "annotations", ["paper_id"])
    op.create_index("ix_annotations_user_id", "annotations", ["user_id"])

    op.create_table(
        "tags",
        uuid_pk(),
        sa.Column(
            "workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("color", sa.String(16)),
        created_at(),
        sa.UniqueConstraint("workspace_id", "name"),
    )
    op.create_table(
        "paper_tags",
        sa.Column("paper_id", pg.UUID(as_uuid=True), sa.ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("tag_id", pg.UUID(as_uuid=True), sa.ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
    )
    op.create_index("ix_paper_tags_tag_id", "paper_tags", ["tag_id"])

    op.create_table(
        "collections",
        uuid_pk(),
        sa.Column(
            "workspace_id", pg.UUID(as_uuid=True), sa.ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("parent_id", pg.UUID(as_uuid=True), sa.ForeignKey("collections.id", ondelete="CASCADE")),
        created_at(),
        updated_at(),
        sa.UniqueConstraint("workspace_id", "parent_id", "name"),
    )
    op.create_index("ix_collections_parent_id", "collections", ["parent_id"])
    op.create_table(
        "paper_collections",
        sa.Column("paper_id", pg.UUID(as_uuid=True), sa.ForeignKey("papers.id", ondelete="CASCADE"), primary_key=True),
        sa.Column(
            "collection_id", pg.UUID(as_uuid=True), sa.ForeignKey("collections.id", ondelete="CASCADE"), primary_key=True
        ),
    )
    op.create_index("ix_paper_collections_collection_id", "paper_collections", ["collection_id"])


def downgrade() -> None:
    for table in (
        "paper_collections", "collections", "paper_tags", "tags", "annotations",
        "paper_chunks", "papers", "workspace_members", "workspaces", "api_keys", "users",
    ):
        op.drop_table(table)
    bind = op.get_bind()
    for name, values in ENUMS.items():
        pg.ENUM(*values, name=name).drop(bind, checkfirst=True)
