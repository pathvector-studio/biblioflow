from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, get_paper_for_user, require_scopes
from app.core.errors import not_found
from app.core.security import SCOPE_READ_PAPERS, SCOPE_WRITE_PAPERS
from app.db.session import get_db
from app.models.annotation import Annotation
from app.models.paper import Paper
from app.models.user import User
from app.models.workspace import WorkspaceMember, WorkspaceRole
from app.schemas.annotation import AnnotationCreate, AnnotationOut, AnnotationPatch

router = APIRouter(tags=["annotations"])

DEFAULT_COLOR = "#ffe066"


def _out(a: Annotation, display_name: str) -> AnnotationOut:
    return AnnotationOut(
        id=a.id, paper_id=a.paper_id, user_id=a.user_id, user_display_name=display_name, kind=a.kind,
        page_number=a.page_number, position=a.position, content_text=a.content_text, comment=a.comment,
        color=a.color, created_at=a.created_at, updated_at=a.updated_at,
    )


async def _owned_annotation(db: AsyncSession, annotation_id: uuid.UUID, principal: Principal) -> tuple[Annotation, str]:
    """Annotation + author name; 404 if the paper is invisible, 403 if not allowed to modify it."""
    row = (
        await db.execute(
            select(Annotation, User.display_name, WorkspaceMember.role)
            .join(Paper, Paper.id == Annotation.paper_id)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Paper.workspace_id)
            .join(User, User.id == Annotation.user_id)
            .where(Annotation.id == annotation_id, WorkspaceMember.user_id == principal.user.id)
        )
    ).first()
    if row is None:
        raise not_found()
    ann, name, role = row
    # Editing needs editor+ (an author later demoted to viewer loses it), and only the author or the owner.
    if not role.at_least(WorkspaceRole.editor) or (ann.user_id != principal.user.id and role != WorkspaceRole.owner):
        raise HTTPException(status_code=403, detail="Not allowed to modify this annotation")
    return ann, name


@router.get("/papers/{paper_id}/annotations", response_model=list[AnnotationOut])
async def list_annotations(
    paper_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> list[AnnotationOut]:
    await get_paper_for_user(db, paper_id, principal, WorkspaceRole.viewer)
    rows = (
        await db.execute(
            select(Annotation, User.display_name)
            .join(User, User.id == Annotation.user_id)
            .where(Annotation.paper_id == paper_id)
            .order_by(Annotation.page_number, Annotation.created_at)
        )
    ).all()
    return [_out(a, n) for a, n in rows]


@router.post("/papers/{paper_id}/annotations", response_model=AnnotationOut, status_code=201)
async def create_annotation(
    paper_id: uuid.UUID,
    body: AnnotationCreate,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> AnnotationOut:
    await get_paper_for_user(db, paper_id, principal, WorkspaceRole.editor)
    ann = Annotation(
        paper_id=paper_id, user_id=principal.user.id, kind=body.kind, page_number=body.page_number,
        position=body.position, content_text=body.content_text, comment=body.comment,
        color=body.color or DEFAULT_COLOR,
    )
    db.add(ann)
    await db.commit()
    await db.refresh(ann)
    return _out(ann, principal.user.display_name)


@router.patch("/annotations/{annotation_id}", response_model=AnnotationOut)
async def patch_annotation(
    annotation_id: uuid.UUID,
    body: AnnotationPatch,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> AnnotationOut:
    ann, name = await _owned_annotation(db, annotation_id, principal)
    for field in body.model_fields_set:
        value = getattr(body, field)
        if field in ("color", "position") and value is None:
            continue  # non-nullable columns
        setattr(ann, field, value)
    await db.commit()
    await db.refresh(ann)
    return _out(ann, name)


@router.delete("/annotations/{annotation_id}", status_code=204)
async def delete_annotation(
    annotation_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    ann, _ = await _owned_annotation(db, annotation_id, principal)
    await db.delete(ann)
    await db.commit()
    return Response(status_code=204)
