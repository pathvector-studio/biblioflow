import enum
import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, TimestampMixin, UUIDPKMixin, pg_enum


class WorkspaceKind(str, enum.Enum):
    personal = "personal"
    shared = "shared"


class WorkspaceRole(str, enum.Enum):
    viewer = "viewer"
    editor = "editor"
    owner = "owner"

    @property
    def rank(self) -> int:
        return {"viewer": 0, "editor": 1, "owner": 2}[self.value]

    def at_least(self, minimum: "WorkspaceRole") -> bool:
        return self.rank >= minimum.rank


class Workspace(UUIDPKMixin, TimestampMixin, Base):
    __tablename__ = "workspaces"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    kind: Mapped[WorkspaceKind] = mapped_column(pg_enum(WorkspaceKind, "workspace_kind"), nullable=False)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )


class WorkspaceMember(CreatedAtMixin, Base):
    __tablename__ = "workspace_members"

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    role: Mapped[WorkspaceRole] = mapped_column(pg_enum(WorkspaceRole, "workspace_role"), nullable=False)
