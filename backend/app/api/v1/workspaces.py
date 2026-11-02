import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    Principal,
    accessible_workspace_ids,
    get_workspace_for_user,
    require_scopes,
)
from app.core.errors import not_found
from app.core.security import SCOPE_READ_PAPERS, SCOPE_WRITE_PAPERS
from app.db.session import get_db
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceKind, WorkspaceMember, WorkspaceRole
from app.schemas.workspace import MemberAdd, MemberOut, WorkspaceCreate, WorkspaceDetail, WorkspaceOut
from app.services.storage.paths import slugify_title

router = APIRouter(prefix="/workspaces", tags=["workspaces"])


def _out(ws: Workspace, role: WorkspaceRole) -> WorkspaceOut:
    return WorkspaceOut(id=ws.id, name=ws.name, slug=ws.slug, kind=ws.kind, role=role)


@router.get("", response_model=list[WorkspaceOut])
async def list_workspaces(
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)), db: AsyncSession = Depends(get_db)
) -> list[WorkspaceOut]:
    rows = await db.execute(
        select(Workspace, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
        .where(
            WorkspaceMember.user_id == principal.user.id,
            Workspace.id.in_(accessible_workspace_ids(principal.user.id)),
        )
        .order_by(Workspace.created_at)
    )
    return [_out(ws, role) for ws, role in rows.all()]


@router.post("", response_model=WorkspaceOut, status_code=201)
async def create_workspace(
    body: WorkspaceCreate,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceOut:
    ws_id = uuid.uuid4()
    ws = Workspace(
        id=ws_id,
        name=body.name.strip(),
        slug=f"{slugify_title(body.name, 40)}-{ws_id.hex[:8]}",
        kind=WorkspaceKind.shared,
        owner_id=principal.user.id,
    )
    db.add(ws)
    await db.flush()
    db.add(WorkspaceMember(workspace_id=ws_id, user_id=principal.user.id, role=WorkspaceRole.owner))
    await db.commit()
    return _out(ws, WorkspaceRole.owner)


@router.get("/{workspace_id}", response_model=WorkspaceDetail)
async def get_workspace(
    workspace_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceDetail:
    ws, role = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.viewer)
    rows = await db.execute(
        select(User, WorkspaceMember.role)
        .join(WorkspaceMember, WorkspaceMember.user_id == User.id)
        .where(WorkspaceMember.workspace_id == ws.id)
        .order_by(User.email)
    )
    members = [MemberOut(user_id=u.id, email=u.email, display_name=u.display_name, role=r) for u, r in rows.all()]
    return WorkspaceDetail(**_out(ws, role).model_dump(), members=members)


@router.post("/{workspace_id}/members", response_model=MemberOut, status_code=201)
async def add_member(
    workspace_id: uuid.UUID,
    body: MemberAdd,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> MemberOut:
    ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.owner)
    if ws.kind == WorkspaceKind.personal:
        raise HTTPException(status_code=400, detail="Personal workspaces cannot have additional members")
    user = (
        await db.execute(select(User).where(User.email == body.email.strip().lower(), User.is_active.is_(True)))
    ).scalar_one_or_none()
    if user is None:
        raise not_found()
    existing = await db.get(WorkspaceMember, (ws.id, user.id))
    if existing is not None:
        raise HTTPException(status_code=409, detail="User is already a member")
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role=body.role))
    await db.commit()
    return MemberOut(user_id=user.id, email=user.email, display_name=user.display_name, role=body.role)
