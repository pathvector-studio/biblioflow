import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    Principal,
    accessible_workspace_ids,
    get_session_principal,
    get_workspace_for_user,
    require_scopes,
)
from app.core.errors import not_found
from app.core.ratelimit import enforce_rate_limit
from app.core.redis import get_redis
from app.core.security import SCOPE_READ_PAPERS, SCOPE_WRITE_PAPERS
from app.db.session import get_db
from app.models.user import User
from app.models.workspace import (
    Workspace,
    WorkspaceInvitation,
    WorkspaceKind,
    WorkspaceMember,
    WorkspaceRole,
)
from app.schemas.workspace import (
    InvitationCreated,
    MemberAdd,
    MemberOut,
    MemberRolePatch,
    WorkspaceCreate,
    WorkspaceDetail,
    WorkspaceInvitationOut,
    WorkspaceOut,
)
from app.services.storage.paths import slugify_title

router = APIRouter(prefix="/workspaces", tags=["workspaces"])

INVITATION_TTL = timedelta(days=14)
INVITATIONS_PER_HOUR = 30


def _reject_personal(ws: Workspace) -> None:
    if ws.kind == WorkspaceKind.personal:
        raise HTTPException(status_code=400, detail="Personal workspaces have no member management")


async def _lock_members(db: AsyncSession, workspace_id: uuid.UUID) -> dict[uuid.UUID, WorkspaceMember]:
    """Members of the workspace, row-locked so concurrent last-owner checks serialize."""
    rows = await db.execute(
        select(WorkspaceMember).where(WorkspaceMember.workspace_id == workspace_id).with_for_update()
    )
    return {m.user_id: m for m in rows.scalars()}


def _owner_count(members: dict[uuid.UUID, WorkspaceMember]) -> int:
    return sum(1 for m in members.values() if m.role == WorkspaceRole.owner)


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
    principal: Principal = Depends(get_session_principal),
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


@router.post("/{workspace_id}/members", response_model=InvitationCreated, status_code=202)
async def invite_member(
    workspace_id: uuid.UUID,
    body: MemberAdd,
    principal: Principal = Depends(get_session_principal),
    db: AsyncSession = Depends(get_db),
) -> InvitationCreated:
    """Create (or refresh) an invitation. The response is identical whether or not the e-mail has an account."""
    ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.owner)
    _reject_personal(ws)
    await enforce_rate_limit(
        get_redis(), "workspace_invite", (str(principal.user.id),), INVITATIONS_PER_HOUR, window=3600
    )
    email = body.email.strip().lower()
    already = (
        await db.execute(
            select(WorkspaceMember.user_id)
            .join(User, User.id == WorkspaceMember.user_id)
            .where(WorkspaceMember.workspace_id == ws.id, User.email == email)
        )
    ).first()
    if already is not None:
        raise HTTPException(status_code=409, detail="User is already a member")
    now = datetime.now(UTC)
    # Upsert: concurrent invitations for the same e-mail must not surface as a 500.
    await db.execute(
        pg_insert(WorkspaceInvitation)
        .values(
            workspace_id=ws.id, email=email, role=body.role, invited_by=principal.user.id,
            created_at=now, expires_at=now + INVITATION_TTL,
        )
        .on_conflict_do_update(
            index_elements=[WorkspaceInvitation.workspace_id, WorkspaceInvitation.email],
            set_={"role": body.role, "invited_by": principal.user.id, "created_at": now, "expires_at": now + INVITATION_TTL},
        )
    )
    await db.commit()
    return InvitationCreated(email=email, role=body.role)


@router.get("/{workspace_id}/invitations", response_model=list[WorkspaceInvitationOut])
async def list_workspace_invitations(
    workspace_id: uuid.UUID,
    principal: Principal = Depends(get_session_principal),
    db: AsyncSession = Depends(get_db),
) -> list[WorkspaceInvitationOut]:
    ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.owner)
    rows = await db.execute(
        select(WorkspaceInvitation)
        .where(WorkspaceInvitation.workspace_id == ws.id, WorkspaceInvitation.expires_at > func.now())
        .order_by(WorkspaceInvitation.created_at)
    )
    return [
        WorkspaceInvitationOut(
            id=i.id, email=i.email, role=i.role, created_at=i.created_at, expires_at=i.expires_at
        )
        for i in rows.scalars()
    ]


@router.delete("/{workspace_id}/invitations/{invitation_id}", status_code=204)
async def revoke_invitation(
    workspace_id: uuid.UUID,
    invitation_id: uuid.UUID,
    principal: Principal = Depends(get_session_principal),
    db: AsyncSession = Depends(get_db),
) -> None:
    ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.owner)
    result = await db.execute(
        delete(WorkspaceInvitation).where(
            WorkspaceInvitation.id == invitation_id, WorkspaceInvitation.workspace_id == ws.id
        )
    )
    if result.rowcount == 0:
        raise not_found()
    await db.commit()


@router.patch("/{workspace_id}/members/{user_id}", response_model=MemberOut)
async def change_member_role(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    body: MemberRolePatch,
    principal: Principal = Depends(get_session_principal),
    db: AsyncSession = Depends(get_db),
) -> MemberOut:
    ws, _ = await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.owner)
    _reject_personal(ws)
    members = await _lock_members(db, ws.id)
    target = members.get(user_id)
    if target is None:
        raise not_found()
    if (
        target.role == WorkspaceRole.owner
        and body.role != WorkspaceRole.owner
        and _owner_count(members) <= 1
    ):
        raise HTTPException(status_code=409, detail="A workspace needs at least one owner")
    target.role = body.role
    user = await db.get(User, user_id)
    await db.commit()
    assert user is not None
    return MemberOut(user_id=user.id, email=user.email, display_name=user.display_name, role=body.role)


@router.delete("/{workspace_id}/members/{user_id}", status_code=204)
async def remove_member(
    workspace_id: uuid.UUID,
    user_id: uuid.UUID,
    principal: Principal = Depends(get_session_principal),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Owners may remove anyone; other members may only remove themselves (leave)."""
    is_self = user_id == principal.user.id
    ws, _ = await get_workspace_for_user(
        db, workspace_id, principal, WorkspaceRole.viewer if is_self else WorkspaceRole.owner
    )
    _reject_personal(ws)
    members = await _lock_members(db, ws.id)
    target = members.get(user_id)
    if target is None:
        raise not_found()
    if target.role == WorkspaceRole.owner and _owner_count(members) <= 1:
        raise HTTPException(status_code=409, detail="A workspace needs at least one owner")
    await db.delete(target)
    await db.commit()
