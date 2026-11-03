from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import Principal, get_workspace_for_user, require_scopes
from app.core.errors import not_found
from app.core.security import SCOPE_READ_PAPERS, SCOPE_WRITE_PAPERS
from app.db.session import get_db
from app.models.collection import Collection, paper_collections
from app.models.tag import Tag, paper_tags
from app.models.workspace import WorkspaceMember, WorkspaceRole
from app.schemas.collection import CollectionCreate, CollectionOut, CollectionPatch, TagOut

router = APIRouter(tags=["collections"])


def _out(c: Collection, paper_count: int) -> CollectionOut:
    return CollectionOut(
        id=c.id, workspace_id=c.workspace_id, name=c.name, parent_id=c.parent_id, paper_count=paper_count,
        created_at=c.created_at, updated_at=c.updated_at,
    )


async def _paper_count(db: AsyncSession, collection_id: uuid.UUID) -> int:
    return (
        await db.execute(
            select(func.count()).select_from(paper_collections).where(paper_collections.c.collection_id == collection_id)
        )
    ).scalar_one()


async def _collection_for_user(
    db: AsyncSession, collection_id: uuid.UUID, principal: Principal, min_role: WorkspaceRole
) -> Collection:
    row = (
        await db.execute(
            select(Collection, WorkspaceMember.role)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Collection.workspace_id)
            .where(Collection.id == collection_id, WorkspaceMember.user_id == principal.user.id)
        )
    ).first()
    if row is None or not row[1].at_least(min_role):
        raise not_found()
    return row[0]


async def _check_parent(db: AsyncSession, workspace_id: uuid.UUID, parent_id: uuid.UUID, self_id: uuid.UUID | None) -> None:
    parents = dict(
        (await db.execute(select(Collection.id, Collection.parent_id).where(Collection.workspace_id == workspace_id))).all()
    )
    if parent_id not in parents:
        raise not_found()
    node: uuid.UUID | None = parent_id
    while node is not None:
        if node == self_id:
            raise HTTPException(status_code=422, detail="A collection cannot be moved under itself or its descendants")
        node = parents.get(node)


@router.get("/tags", response_model=list[TagOut])
async def list_tags(
    workspace_id: uuid.UUID = Query(),
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> list[TagOut]:
    await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.viewer)
    rows = (
        await db.execute(
            select(Tag, func.count(paper_tags.c.paper_id))
            .outerjoin(paper_tags, paper_tags.c.tag_id == Tag.id)
            .where(Tag.workspace_id == workspace_id)
            .group_by(Tag.id)
            .order_by(func.lower(Tag.name), Tag.name)
        )
    ).all()
    return [TagOut(id=t.id, name=t.name, color=t.color, paper_count=n) for t, n in rows]


@router.get("/collections", response_model=list[CollectionOut])
async def list_collections(
    workspace_id: uuid.UUID = Query(),
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> list[CollectionOut]:
    await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.viewer)
    rows = (
        await db.execute(
            select(Collection, func.count(paper_collections.c.paper_id))
            .outerjoin(paper_collections, paper_collections.c.collection_id == Collection.id)
            .where(Collection.workspace_id == workspace_id)
            .group_by(Collection.id)
            .order_by(func.lower(Collection.name), Collection.name)
        )
    ).all()
    return [_out(c, n) for c, n in rows]


@router.post("/collections", response_model=CollectionOut, status_code=201)
async def create_collection(
    body: CollectionCreate,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> CollectionOut:
    await get_workspace_for_user(db, body.workspace_id, principal, WorkspaceRole.editor)
    if body.parent_id is not None:
        await _check_parent(db, body.workspace_id, body.parent_id, None)
    col = Collection(workspace_id=body.workspace_id, name=body.name.strip(), parent_id=body.parent_id)
    db.add(col)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Collection name already in use") from None
    await db.refresh(col)
    return _out(col, 0)


@router.patch("/collections/{collection_id}", response_model=CollectionOut)
async def patch_collection(
    collection_id: uuid.UUID,
    body: CollectionPatch,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> CollectionOut:
    col = await _collection_for_user(db, collection_id, principal, WorkspaceRole.editor)
    fields = body.model_fields_set
    if "name" in fields and body.name is not None:
        col.name = body.name.strip()
    if "parent_id" in fields:
        if body.parent_id is not None:
            await _check_parent(db, col.workspace_id, body.parent_id, col.id)
        col.parent_id = body.parent_id
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Collection name already in use") from None
    await db.refresh(col)
    return _out(col, await _paper_count(db, col.id))


@router.delete("/collections/{collection_id}", status_code=204)
async def delete_collection(
    collection_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    col = await _collection_for_user(db, collection_id, principal, WorkspaceRole.editor)
    await db.delete(col)  # children and paper links go via ON DELETE CASCADE; papers are kept
    await db.commit()
    return Response(status_code=204)
