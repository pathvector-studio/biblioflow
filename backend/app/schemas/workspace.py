import uuid
from datetime import datetime

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


class InvitationCreated(BaseModel):
    email: str
    role: WorkspaceRole
    status: str = "pending"


class MemberRolePatch(BaseModel):
    role: WorkspaceRole


class MyInvitationOut(BaseModel):
    id: uuid.UUID
    workspace_name: str
    role: WorkspaceRole
    invited_by_name: str
    expires_at: datetime


class WorkspaceInvitationOut(BaseModel):
    id: uuid.UUID
    email: str
    role: WorkspaceRole
    created_at: datetime
    expires_at: datetime
