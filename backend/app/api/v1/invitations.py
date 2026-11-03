import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, get_session_principal
from app.core.errors import not_found
from app.db.session import get_db
from app.models.user import User
from app.models.workspace import Workspace, WorkspaceInvitation, WorkspaceMember
from app.schemas.workspace import MyInvitationOut, WorkspaceOut

router = APIRouter(prefix="/invitations", tags=["invitations"])


async def _own_invitation(db: AsyncSession, invitation_id: uuid.UUID, principal: Principal) -> WorkspaceInvitation:
    """Pending, unexpired invitation addressed to the caller's e-mail; 404 otherwise (including others')."""
    inv = (
        await db.execute(
            select(WorkspaceInvitation).where(
                WorkspaceInvitation.id == invitation_id,
                WorkspaceInvitation.email == principal.user.email,
                WorkspaceInvitation.expires_at > datetime.now(UTC),
            )
        )
    ).scalar_one_or_none()
    if inv is None:
        raise not_found()
    return inv


@router.get("", response_model=list[MyInvitationOut])
async def list_my_invitations(
    principal: Principal = Depends(get_session_principal), db: AsyncSession = Depends(get_db)
) -> list[MyInvitationOut]:
    rows = await db.execute(
        select(WorkspaceInvitation, Workspace.name, User.display_name)
        .join(Workspace, Workspace.id == WorkspaceInvitation.workspace_id)
        .join(User, User.id == WorkspaceInvitation.invited_by)
        .where(
            WorkspaceInvitation.email == principal.user.email,
            WorkspaceInvitation.expires_at > datetime.now(UTC),
        )
        .order_by(WorkspaceInvitation.created_at.desc())
    )
    return [
        MyInvitationOut(
            id=inv.id, workspace_name=name, role=inv.role, invited_by_name=inviter, expires_at=inv.expires_at
        )
        for inv, name, inviter in rows.all()
    ]


@router.post("/{invitation_id}/accept", response_model=WorkspaceOut)
async def accept_invitation(
    invitation_id: uuid.UUID,
    principal: Principal = Depends(get_session_principal),
    db: AsyncSession = Depends(get_db),
) -> WorkspaceOut:
    inv = await _own_invitation(db, invitation_id, principal)
    ws = await db.get(Workspace, inv.workspace_id)
    assert ws is not None
    # ON CONFLICT DO NOTHING: an existing membership keeps its role, and a double accept is not a 500.
    await db.execute(
        pg_insert(WorkspaceMember)
        .values(workspace_id=ws.id, user_id=principal.user.id, role=inv.role)
        .on_conflict_do_nothing()
    )
    role = (
        await db.execute(
            select(WorkspaceMember.role).where(
                WorkspaceMember.workspace_id == ws.id, WorkspaceMember.user_id == principal.user.id
            )
        )
    ).scalar_one()
    await db.execute(delete(WorkspaceInvitation).where(WorkspaceInvitation.id == inv.id))
    await db.commit()
    return WorkspaceOut(id=ws.id, name=ws.name, slug=ws.slug, kind=ws.kind, role=role)


@router.post("/{invitation_id}/decline", status_code=204)
async def decline_invitation(
    invitation_id: uuid.UUID,
    principal: Principal = Depends(get_session_principal),
    db: AsyncSession = Depends(get_db),
) -> None:
    inv = await _own_invitation(db, invitation_id, principal)
    await db.execute(delete(WorkspaceInvitation).where(WorkspaceInvitation.id == inv.id))
    await db.commit()
