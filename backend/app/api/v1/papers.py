from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import PurePosixPath
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from starlette.datastructures import UploadFile as StarletteUploadFile
from sqlalchemy import and_, case, exists, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import (
    Principal,
    accessible_workspace_ids,
    get_paper_for_user,
    get_workspace_for_user,
    require_scopes,
)
from app.core.config import get_settings
from app.core.errors import not_found
from app.core.net import BlockedDestination, safe_client
from app.core.ratelimit import enforce_rate_limit
from app.core.redis import get_redis
from app.core.security import SCOPE_READ_PAPERS, SCOPE_WRITE_PAPERS
from app.db.session import get_db
from app.db.sql import LIKE_ESCAPE, escape_like
from app.models.chunk import PaperChunk
from app.models.collection import Collection, paper_collections
from app.models.paper import IdentifierType, Paper, PaperStatus
from app.models.tag import Tag, paper_tags
from app.models.workspace import Workspace, WorkspaceKind, WorkspaceRole
from app.schemas.chunk import ChunkOut, ChunksOut, NoteAppendIn, NoteAppendOut
from app.schemas.metadata import Author, LookupResult, PaperMetadata, UpstreamError
from app.schemas.paper import ImportUrlIn, MetadataOnlyIn, PaperDetail, PaperLight, PaperPatch
from app.services import ingest, remote_pdf
from app.services.ingest import IngestError
from app.services.metadata import get_resolver, identifiers
from app.services.storage.files import TooLarge, receive_stream
from app.services.storage.paths import PathTraversalError, generate_internal_identifier, resolve_in_library

router = APIRouter(prefix="/papers", tags=["papers"])

CHUNK = 64 * 1024
IMPORT_TOTAL_TIMEOUT = 120.0

# Tests replace this with a factory returning a client backed by httpx.MockTransport.
_http_client_factory = safe_client


async def ingest_error_handler(request: Request, exc: IngestError) -> JSONResponse:
    return JSONResponse(exc.body(), status_code=exc.status_code)


# ------------------------------------------------------------------ helpers
async def _workspace(
    db: AsyncSession, principal: Principal, workspace_id: uuid.UUID | None, min_role: WorkspaceRole
) -> Workspace:
    if workspace_id is None:
        workspace_id = (
            await db.execute(
                select(Workspace.id).where(
                    Workspace.kind == WorkspaceKind.personal, Workspace.owner_id == principal.user.id
                )
            )
        ).scalar_one_or_none()
        if workspace_id is None:
            raise not_found()
    ws, _ = await get_workspace_for_user(db, workspace_id, principal, min_role)
    return ws


def _authors(raw: list[Any] | None) -> list[Author]:
    return [Author.model_validate(a) for a in (raw or [])]


async def _tags_by_paper(db: AsyncSession, paper_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[str]]:
    out: dict[uuid.UUID, list[str]] = {pid: [] for pid in paper_ids}
    if not paper_ids:
        return out
    rows = await db.execute(
        select(paper_tags.c.paper_id, Tag.name)
        .join(Tag, Tag.id == paper_tags.c.tag_id)
        .where(paper_tags.c.paper_id.in_(paper_ids))
        .order_by(func.lower(Tag.name))
    )
    for pid, name in rows.all():
        out[pid].append(name)
    return out


async def _detail(db: AsyncSession, paper: Paper) -> PaperDetail:
    await db.refresh(paper)  # server-side defaults / onupdate columns may be expired after a flush
    tags = (await _tags_by_paper(db, [paper.id]))[paper.id]
    cids = (
        await db.execute(select(paper_collections.c.collection_id).where(paper_collections.c.paper_id == paper.id))
    ).scalars()
    return PaperDetail(
        id=paper.id,
        workspace_id=paper.workspace_id,
        identifier=paper.identifier,
        identifier_type=paper.identifier_type,
        doi=paper.doi,
        arxiv_id=paper.arxiv_id,
        citekey=paper.citekey,
        title=paper.title,
        authors=_authors(paper.authors),
        year=paper.year,
        venue=paper.venue,
        abstract=paper.abstract,
        bibtex=paper.bibtex,
        notes_md=paper.notes_md,
        tags=tags,
        status=paper.status,
        has_pdf=paper.has_pdf,
        relative_file_path=paper.relative_file_path,
        file_size=paper.file_size,
        file_sha256=paper.file_sha256 if paper.has_pdf else None,
        page_count=paper.page_count,
        index_status=paper.index_status,
        collection_ids=sorted(cids),
        created_at=paper.created_at,
        updated_at=paper.updated_at,
    )


def _like_pattern(q: str) -> str:
    return "%" + escape_like(q) + "%"


def _author_matches(pat: str) -> Any:
    """EXISTS over the authors JSONB array: family, given, or "given family" matches ``pat``."""
    elem = func.jsonb_array_elements(Paper.authors).table_valued("value").alias("author")
    value = elem.c.value
    full = func.concat_ws(" ", value.op("->>")("given"), value.op("->>")("family"))
    return exists().select_from(elem).where(
        or_(
            value.op("->>")("family").ilike(pat, escape=LIKE_ESCAPE),
            value.op("->>")("given").ilike(pat, escape=LIKE_ESCAPE),
            full.ilike(pat, escape=LIKE_ESCAPE),
        )
    )


def filter_conditions(
    *,
    q: str | None = None,
    tag: str | None = None,
    collection_id: uuid.UUID | None = None,
    year: int | None = None,
    status: PaperStatus | None = None,
) -> list[Any]:
    """The filter conditions shared by the list, export and auto-export queries (no access check)."""
    conds: list[Any] = []
    if q and q.strip():
        pat = _like_pattern(q.strip())
        conds.append(
            or_(
                Paper.title.ilike(pat, escape=LIKE_ESCAPE),
                Paper.identifier.ilike(pat, escape=LIKE_ESCAPE),
                Paper.citekey.ilike(pat, escape=LIKE_ESCAPE),
                _author_matches(pat),
            )
        )
    if tag:
        conds.append(
            exists().where(
                paper_tags.c.paper_id == Paper.id,
                paper_tags.c.tag_id == Tag.id,
                func.lower(Tag.name) == tag.strip().lower(),
            )
        )
    if collection_id is not None:
        conds.append(
            exists().where(paper_collections.c.paper_id == Paper.id, paper_collections.c.collection_id == collection_id)
        )
    if year is not None:
        conds.append(Paper.year == year)
    if status is not None:
        conds.append(Paper.status == status)
    return conds


async def paper_filter(
    db: AsyncSession,
    principal: Principal,
    *,
    q: str | None = None,
    tag: str | None = None,
    collection_id: uuid.UUID | None = None,
    year: int | None = None,
    status: PaperStatus | None = None,
    workspace_id: uuid.UUID | None = None,
) -> Any:
    """WHERE clause shared by the list and export endpoints (always limited to the caller's workspaces)."""
    conds = [Paper.workspace_id.in_(accessible_workspace_ids(principal.user.id))]
    if workspace_id is not None:
        await get_workspace_for_user(db, workspace_id, principal, WorkspaceRole.viewer)
        conds.append(Paper.workspace_id == workspace_id)
    conds.extend(filter_conditions(q=q, tag=tag, collection_id=collection_id, year=year, status=status))
    return and_(*conds)


async def _rate_limit(principal: Principal, name: str, limit: int) -> None:
    await enforce_rate_limit(get_redis(), name, (str(principal.user.id),), limit)


async def _upload_chunks(file: UploadFile) -> AsyncIterator[bytes]:
    while chunk := await file.read(CHUNK):
        yield chunk


# ------------------------------------------------------------------ list / lookup / create
@router.get("/light", response_model=list[PaperLight])
async def list_papers_light(
    response: Response,
    q: str | None = Query(None, max_length=200),
    tag: str | None = Query(None, max_length=100),
    collection_id: uuid.UUID | None = None,
    year: int | None = Query(None, ge=1, le=9999),
    status: PaperStatus | None = None,
    workspace_id: uuid.UUID | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> list[PaperLight]:
    where = await paper_filter(
        db, principal, q=q, tag=tag, collection_id=collection_id, year=year, status=status, workspace_id=workspace_id
    )

    total = (await db.execute(select(func.count()).select_from(Paper).where(where))).scalar_one()
    rows = (
        await db.execute(
            select(
                Paper.id, Paper.identifier, Paper.citekey, Paper.title, Paper.authors, Paper.year, Paper.venue,
                Paper.status, Paper.has_pdf, Paper.relative_file_path,
            )
            .where(where)
            .order_by(Paper.created_at.desc(), Paper.id)
            .limit(limit)
            .offset(offset)
        )
    ).all()
    tags = await _tags_by_paper(db, [r.id for r in rows])
    response.headers["X-Total-Count"] = str(total)
    return [
        PaperLight(
            id=r.id, identifier=r.identifier, citekey=r.citekey, title=r.title, authors=_authors(r.authors),
            year=r.year, venue=r.venue, tags=tags[r.id], status=r.status, has_pdf=r.has_pdf,
            relative_file_path=r.relative_file_path,
        )
        for r in rows
    ]


@router.get("/lookup", response_model=LookupResult)
async def lookup(
    query: str = Query(..., min_length=1, max_length=500),
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    resolver=Depends(get_resolver),
) -> LookupResult:
    await _rate_limit(principal, "papers_lookup", 30)
    try:
        return await resolver.resolve(query.strip())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc) or "Invalid query") from None
    except UpstreamError:
        raise HTTPException(status_code=502, detail="upstream error") from None


@router.post("/metadata-only", response_model=PaperDetail, status_code=201)
async def create_metadata_only(
    body: MetadataOnlyIn,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
    resolver=Depends(get_resolver),
) -> PaperDetail:
    await _rate_limit(principal, "papers_metadata_only", 30)
    ws = await _workspace(db, principal, body.workspace_id, WorkspaceRole.editor)
    if body.identifier is not None:
        if identifiers.parse_query(body.identifier).kind == "title":
            raise HTTPException(status_code=422, detail="identifier must be a DOI or arXiv id")
        try:
            result = await resolver.resolve(body.identifier, limit=1)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc) or "Invalid identifier") from None
        except UpstreamError:
            raise HTTPException(status_code=502, detail="upstream error") from None
        if not result.results:
            raise HTTPException(status_code=404, detail="No metadata found")
        meta = PaperMetadata.model_validate(result.results[0].model_dump(exclude={"bibtex"}))
    else:
        assert body.title is not None
        meta = PaperMetadata(
            identifier=generate_internal_identifier(),
            identifier_type=IdentifierType.internal,
            title=body.title,
            authors=body.authors,
            year=body.year,
            venue=body.venue,
            source="manual",
        )
    paper = await ingest.create_metadata_only(db, workspace=ws, user_id=principal.user.id, meta=meta, tags=body.tags)
    return await _detail(db, paper)


# ------------------------------------------------------------------ PDF intake
_PDF_CONTENT_TYPES = {"application/pdf", "application/x-pdf", "application/octet-stream", ""}
_UPLOAD_OPENAPI = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {
                        "file": {"type": "string", "format": "binary", "description": "The PDF (.pdf, %PDF- magic checked)"},
                        "workspace_id": {"type": "string", "format": "uuid"},
                        "paper_id": {"type": "string", "format": "uuid", "description": "Attach to an existing wishlist paper"},
                        "identifier": {"type": "string", "maxLength": 500, "description": "DOI or arXiv id hint"},
                        "status": {"type": "string", "enum": [s.value for s in PaperStatus]},
                    },
                }
            }
        },
    }
}


def _form_uuid(form: Any, name: str) -> uuid.UUID | None:
    raw = form.get(name)
    if raw is None or raw == "":
        return None
    try:
        return uuid.UUID(str(raw))
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Invalid {name}") from None


@router.post("/upload", response_model=PaperDetail, status_code=201, openapi_extra=_UPLOAD_OPENAPI)
async def upload_paper(
    request: Request,
    workspace_id: uuid.UUID | None = Query(None, description="Target workspace (a form field of the same name also works)"),
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
    resolver=Depends(get_resolver),
) -> PaperDetail:
    """Upload a PDF (multipart/form-data). Auth and the rate limit run before the body is parsed."""
    await _rate_limit(principal, "papers_upload", 60)
    form = await request.form(max_files=1, max_fields=10)
    try:
        file = form.get("file")
        if not isinstance(file, StarletteUploadFile):
            raise HTTPException(status_code=422, detail="Missing file")
        ws = await _workspace(db, principal, _form_uuid(form, "workspace_id") or workspace_id, WorkspaceRole.editor)
        paper_id = _form_uuid(form, "paper_id")
        identifier = form.get("identifier")
        identifier = identifier.strip() if isinstance(identifier, str) else ""
        if len(identifier) > 500:
            raise HTTPException(status_code=422, detail="identifier too long")
        status_raw = form.get("status")
        try:
            status = PaperStatus(status_raw) if isinstance(status_raw, str) and status_raw else None
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid status") from None
        name = file.filename or ""
        ctype = (file.content_type or "").split(";", 1)[0].strip().lower()
        if not name.lower().endswith(".pdf") or ctype not in _PDF_CONTENT_TYPES:
            raise HTTPException(status_code=415, detail="Only PDF files are accepted")
        settings = get_settings()
        try:
            staged = await receive_stream(_upload_chunks(file), settings.library_root, settings.max_upload_bytes)
        except TooLarge:
            raise HTTPException(status_code=413, detail="File too large") from None
        paper = await ingest.ingest_pdf(
            db,
            staged=staged,
            workspace=ws,
            user_id=principal.user.id,
            resolver=resolver,
            original_filename=ingest.safe_original_name(name),
            identifier_hint=identifier or None,
            paper_id=paper_id,
            status=status,
        )
    finally:
        await form.close()
    return await _detail(db, paper)


@router.post("/import-url", response_model=PaperDetail, status_code=201)
async def import_url(
    body: ImportUrlIn,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
    resolver=Depends(get_resolver),
) -> PaperDetail:
    """Download a PDF and register it. ``url`` may be a PDF URL, a landing page (its
    ``citation_pdf_url`` is followed), ``arxiv.org/abs/...``, a DOI, an arXiv id or a PII.
    Identifiers found in the input drive metadata resolution and attach to a matching wishlist entry."""
    settings = get_settings()
    if not settings.url_import_enabled:
        raise not_found()
    await _rate_limit(principal, "papers_import_url", 10)
    ws = await _workspace(db, principal, body.workspace_id, WorkspaceRole.editor)
    try:
        source = remote_pdf.resolve_source(body.url)
        staged, filename = await asyncio.wait_for(
            remote_pdf.fetch_pdf(
                _http_client_factory, source.url, root=settings.library_root, max_bytes=settings.max_upload_bytes
            ),
            IMPORT_TOTAL_TIMEOUT,
        )
    except remote_pdf.RemoteFetchError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    except BlockedDestination:
        raise HTTPException(status_code=400, detail="URL not allowed") from None
    except TooLarge:
        raise HTTPException(status_code=413, detail="File too large") from None
    except (httpx.HTTPError, TimeoutError):
        raise HTTPException(status_code=502, detail="Failed to fetch URL") from None
    paper = await ingest.ingest_pdf(
        db,
        staged=staged,
        workspace=ws,
        user_id=principal.user.id,
        resolver=resolver,
        original_filename=ingest.safe_original_name(filename),
        identifier_hint=source.identifier_hint,
        identifier_hint_strict=False,
        paper_id=body.paper_id,
    )
    return await _detail(db, paper)


# ------------------------------------------------------------------ single paper
@router.get("/{paper_id}", response_model=PaperDetail)
async def get_paper(
    paper_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> PaperDetail:
    paper = await get_paper_for_user(db, paper_id, principal, WorkspaceRole.viewer)
    return await _detail(db, paper)


MAX_CHUNK_PAGES_PER_REQUEST = 10
MAX_NOTES_CHARS = 500_000


@router.get("/{paper_id}/chunks", response_model=ChunksOut)
async def get_paper_chunks(
    paper_id: uuid.UUID,
    page_from: int = Query(1, ge=1),
    page_to: int | None = Query(None, ge=1),
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> ChunksOut:
    """Extracted text and coordinates for ``page_from``..``page_to`` (at most 10 pages, default one page)."""
    paper = await get_paper_for_user(db, paper_id, principal, WorkspaceRole.viewer)
    last = page_from if page_to is None else page_to
    if last < page_from or last - page_from + 1 > MAX_CHUNK_PAGES_PER_REQUEST:
        raise HTTPException(
            status_code=422, detail=f"page range must be 1..{MAX_CHUNK_PAGES_PER_REQUEST} pages and page_to >= page_from"
        )
    rows = (
        await db.execute(
            select(PaperChunk)
            .where(PaperChunk.paper_id == paper.id, PaperChunk.page_number.between(page_from, last))
            .order_by(PaperChunk.page_number, PaperChunk.chunk_index)
        )
    ).scalars()
    return ChunksOut(
        paper_id=paper.id, index_status=paper.index_status, page_count=paper.page_count,
        file_sha256=paper.file_sha256 if paper.has_pdf else None, page_from=page_from,
        page_to=last, chunks=[ChunkOut.model_validate(c, from_attributes=True) for c in rows],
    )


@router.post("/{paper_id}/notes/append", response_model=NoteAppendOut)
async def append_note(
    paper_id: uuid.UUID,
    body: NoteAppendIn,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> NoteAppendOut:
    """Append to ``notes_md`` in SQL so a note being edited in the UI is never overwritten."""
    paper = await get_paper_for_user(db, paper_id, principal, WorkspaceRole.editor)
    added = len(body.text)
    new_notes = case((Paper.notes_md == "", body.text), else_=Paper.notes_md + "\n\n" + body.text)
    length = (
        await db.execute(
            update(Paper)
            .where(Paper.id == paper.id, func.length(Paper.notes_md) + added + 2 <= MAX_NOTES_CHARS)
            .values(notes_md=new_notes)
            .returning(func.length(Paper.notes_md))
        )
    ).scalar_one_or_none()
    if length is None:
        await db.rollback()
        raise HTTPException(status_code=422, detail="Notes would exceed the size limit")
    await db.commit()
    return NoteAppendOut(paper_id=paper.id, notes_length=length)


_BIB_FIELDS = {"title", "authors", "year", "venue", "citekey"}


@router.patch("/{paper_id}", response_model=PaperDetail)
async def patch_paper(
    paper_id: uuid.UUID,
    body: PaperPatch,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> PaperDetail:
    paper = await get_paper_for_user(db, paper_id, principal, WorkspaceRole.editor)
    fields = body.model_fields_set

    if "collection_ids" in fields and body.collection_ids is not None:
        wanted = set(body.collection_ids)
        if wanted:
            found = set(
                (
                    await db.execute(
                        select(Collection.id).where(Collection.id.in_(wanted), Collection.workspace_id == paper.workspace_id)
                    )
                ).scalars()
            )
            if found != wanted:
                raise HTTPException(status_code=422, detail="Unknown collection")
    if "citekey" in fields and body.citekey != paper.citekey:
        clash = (
            await db.execute(
                select(Paper.id).where(
                    Paper.workspace_id == paper.workspace_id, Paper.citekey == body.citekey, Paper.id != paper.id
                )
            )
        ).first()
        if clash is not None:
            raise HTTPException(status_code=409, detail="Citekey already in use")

    try:
        for name in ("title", "authors", "year", "venue", "abstract", "status", "citekey", "notes_md"):
            if name in fields:
                value = getattr(body, name)
                if name == "authors":
                    value = [a.model_dump() for a in value]
                setattr(paper, name, value)
        if fields & _BIB_FIELDS:
            ingest.regenerate_bibtex(paper)
        if "tags" in fields and body.tags is not None:
            await ingest.set_paper_tags(db, paper, body.tags)
        if "collection_ids" in fields and body.collection_ids is not None:
            await db.execute(paper_collections.delete().where(paper_collections.c.paper_id == paper.id))
            if body.collection_ids:
                await db.execute(
                    paper_collections.insert(),
                    [{"paper_id": paper.id, "collection_id": c} for c in set(body.collection_ids)],
                )
        await db.commit()
    except IngestError:
        await db.rollback()
        raise
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Conflict") from None
    return await _detail(db, paper)


@router.delete("/{paper_id}", status_code=204)
async def delete_paper(
    paper_id: uuid.UUID,
    principal: Principal = Depends(require_scopes(SCOPE_WRITE_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> Response:
    paper = await get_paper_for_user(db, paper_id, principal, WorkspaceRole.editor)
    rel = paper.relative_file_path
    await db.delete(paper)  # annotations, tag/collection links and chunks go via ON DELETE CASCADE
    await db.flush()
    orphaned = False
    if rel:
        orphaned = (await db.execute(select(Paper.id).where(Paper.relative_file_path == rel).limit(1))).first() is None
    await db.commit()
    if orphaned and rel:
        try:
            resolve_in_library(rel, get_settings().library_root).unlink(missing_ok=True)
        except (PathTraversalError, OSError):
            pass  # the DB row is already gone; a stray file is harmless
    return Response(status_code=204)


# A paper's PDF never changes once attached (a second upload is a 409), so the browser may keep it.
# "no-cache" still sends every open to the API (access is re-checked), which answers 304 from the ETag.
FILE_CACHE_CONTROL = "private, no-cache"


def _etag_matches(header: str | None, etag: str) -> bool:
    if not header:
        return False
    return header.strip() == "*" or etag in {t.strip().removeprefix("W/") for t in header.split(",")}


@router.get("/{paper_id}/file", response_model=None)
async def get_paper_file(
    paper_id: uuid.UUID,
    request: Request,
    principal: Principal = Depends(require_scopes(SCOPE_READ_PAPERS)),
    db: AsyncSession = Depends(get_db),
) -> FileResponse | Response:
    paper = await get_paper_for_user(db, paper_id, principal, WorkspaceRole.viewer)
    if not paper.has_pdf or not paper.relative_file_path:
        raise not_found()
    try:
        path = resolve_in_library(paper.relative_file_path, get_settings().library_root)
    except PathTraversalError:
        raise not_found() from None
    if not path.is_file():
        raise not_found()
    headers = {"Cache-Control": FILE_CACHE_CONTROL}
    if paper.file_sha256:
        headers["ETag"] = f'"{paper.file_sha256}"'
        if _etag_matches(request.headers.get("if-none-match"), headers["ETag"]):
            return Response(status_code=304, headers=headers)
    filename = re.sub(r"[^A-Za-z0-9._-]", "_", PurePosixPath(paper.relative_file_path).name) or "paper.pdf"
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=filename,
        content_disposition_type="inline",
        headers=headers,
    )
