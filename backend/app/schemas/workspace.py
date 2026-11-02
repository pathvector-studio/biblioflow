import uuid

from pydantic import BaseModel, EmailStr, Field

from app.models.workspace import WorkspaceKind, WorkspaceRole


class WorkspaceOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    kind: WorkspaceKind
    role: WorkspaceRole


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: str
    role: WorkspaceRole


class WorkspaceDetail(WorkspaceOut):
    members: list[MemberOut]


class MemberAdd(BaseModel):
    email: EmailStr
    role: WorkspaceRole = WorkspaceRole.viewer
