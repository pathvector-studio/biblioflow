"""Shared ingest pipeline for upload, import-url and the watch folder, plus metadata-only creation."""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import queue
from app.core.config import get_settings
from app.db.sql import LIKE_ESCAPE, escape_like
from app.models.paper import IdentifierType, IndexStatus, Paper, PaperStatus
from app.models.tag import Tag, paper_tags
from app.models.workspace import Workspace
from app.schemas.metadata import Author, MetadataResolver, PaperMetadata, UpstreamError
from app.schemas.paper import MAX_TAG_LEN, MAX_TAGS
from app.services.metadata import identifiers
from app.services.metadata.bibtex import citekey_base, make_bibtex
from app.services.pdf import InvalidPdf, PdfInfo, has_pdf_magic, inspect_pdf_isolated
from app.services.storage.files import PlacementError, StagedFile, discard, place_file
from app.services.storage.paths import (
    PathTraversalError,
    build_relative_path,
    generate_internal_identifier,
)

logger = logging.getLogger("biblioflow.ingest")

MAX_TITLE_LEN = 1000
CITEKEY_CONSTRAINT = "uq_papers_workspace_id_citekey"
MAX_CITEKEY_ATTEMPTS = 5
TITLE_MATCH_RATIO = 0.6
MIN_TITLE_WORD_LEN = 3


class IngestError(Exception):
    """Domain error with an HTTP status; the API maps it to a response, the watcher to a rejection reason."""

    def __init__(self, status_code: int, detail: str, **extra: Any) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail
        self.extra = extra

    def body(self) -> dict[str, Any]:
        return {"detail": self.detail, **{k: str(v) if isinstance(v, uuid.UUID) else v for k, v in self.extra.items()}}


# ------------------------------------------------------------------ helpers
def meta_from_paper(paper: Paper) -> PaperMetadata:
    return PaperMetadata(
        identifier=paper.identifier,
        identifier_type=paper.identifier_type,
        doi=paper.doi,
        arxiv_id=paper.arxiv_id,
        title=paper.title,
        authors=[Author.model_validate(a) for a in (paper.authors or [])],
        year=paper.year,
        venue=paper.venue,
        abstract=paper.abstract,
        source="manual",
    )


def regenerate_bibtex(paper: Paper) -> None:
    paper.bibtex = make_bibtex(meta_from_paper(paper), paper.citekey or "paper")


async def unique_citekey(
    db: AsyncSession, workspace_id: uuid.UUID, meta: PaperMetadata, exclude_id: uuid.UUID | None = None
) -> str:
    """``citekey_base`` plus the first free suffix (``a``..``z``, then numbers) within the workspace."""
    base = citekey_base(meta) or "paper"
    like = escape_like(base) + "%"
    stmt = select(Paper.citekey).where(Paper.workspace_id == workspace_id, Paper.citekey.like(like, escape=LIKE_ESCAPE))
    if exclude_id is not None:
        stmt = stmt.where(Paper.id != exclude_id)
    taken = {c for (c,) in (await db.execute(stmt)).all()}
    candidates = [base] + [base + c for c in "abcdefghijklmnopqrstuvwxyz"] + [f"{base}{n}" for n in range(2, 1000)]
    for cand in candidates:
        if cand not in taken:
            return cand
    return f"{base}-{uuid.uuid4().hex[:6]}"


def normalize_tags(names: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in names:
        name = " ".join(raw.split()) if isinstance(raw, str) else ""
        if not name:
            continue
        if len(name) > MAX_TAG_LEN:
            raise IngestError(422, f"tag longer than {MAX_TAG_LEN} characters")
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append(name)
    if len(out) > MAX_TAGS:
        raise IngestError(422, f"at most {MAX_TAGS} tags")
    return out


async def set_paper_tags(db: AsyncSession, paper: Paper, names: list[str]) -> None:
    """Replace the paper's tags with ``names`` (case-insensitive dedupe; missing tags are created in its workspace).

    The paper row must already be flushed.
    """
    wanted = normalize_tags(names)
    await db.execute(delete(paper_tags).where(paper_tags.c.paper_id == paper.id))
    if not wanted:
        return
    await db.execute(
        pg_insert(Tag).values([{"workspace_id": paper.workspace_id, "name": n} for n in wanted]).on_conflict_do_nothing()
    )
    rows = (
        await db.execute(
            select(Tag).where(Tag.workspace_id == paper.workspace_id, func.lower(Tag.name).in_([n.lower() for n in wanted]))
        )
    ).scalars()
    by_lower: dict[str, uuid.UUID] = {}
    for t in rows:
        by_lower.setdefault(t.name.lower(), t.id)  # exact-case duplicates collapse onto one tag
    ids = {by_lower[n.lower()] for n in wanted if n.lower() in by_lower}
    if ids:
        await db.execute(pg_insert(paper_tags).values([{"paper_id": paper.id, "tag_id": i} for i in ids]))


def _conflict_from(exc: IntegrityError) -> IngestError:
    return IngestError(409, "Conflict: paper identifier or citekey already exists")


def _violates(exc: IntegrityError, constraint: str) -> bool:
    """True if the DB error was raised for the named constraint (driver attribute, else message text)."""
    orig = exc.orig
    for err in (orig, getattr(orig, "__cause__", None)):
        if getattr(err, "constraint_name", None) == constraint:
            return True
    return constraint in str(orig)


async def _flush_retrying_citekey(db: AsyncSession, attempt: Callable[[], Awaitable[None]]) -> None:
    """Run ``attempt`` (which assigns a citekey and flushes) in a SAVEPOINT.

    A concurrent request can take the same citekey between our SELECT and INSERT; on that specific unique
    violation, ``attempt`` is re-run (and picks the next free key). Other integrity errors propagate.
    """
    for n in range(MAX_CITEKEY_ATTEMPTS):
        try:
            async with db.begin_nested():
                await attempt()
            return
        except IntegrityError as exc:
            if n + 1 < MAX_CITEKEY_ATTEMPTS and _violates(exc, CITEKEY_CONSTRAINT):
                logger.info("citekey collision, retrying (%d)", n + 1)
                continue
            raise


async def _find_by_identifier(db: AsyncSession, workspace_id: uuid.UUID, identifier: str) -> Paper | None:
    return (
        await db.execute(select(Paper).where(Paper.workspace_id == workspace_id, Paper.identifier == identifier))
    ).scalar_one_or_none()


def _new_paper_fields(meta: PaperMetadata) -> dict[str, Any]:
    return {
        "identifier": meta.identifier,
        "identifier_type": meta.identifier_type,
        "doi": meta.doi,
        "arxiv_id": meta.arxiv_id,
        "title": meta.title[:MAX_TITLE_LEN],
        "authors": [a.model_dump() for a in meta.authors],
        "year": meta.year,
        "venue": meta.venue,
        "abstract": meta.abstract,
    }


async def _resolve_first(resolver: MetadataResolver, query: str) -> PaperMetadata | None:
    try:
        result = await resolver.resolve(query, limit=1)
    except (UpstreamError, ValueError):
        logger.warning("metadata resolution failed for %r", query, exc_info=True)
        return None
    except Exception:  # noqa: BLE001 - never let a metadata backend abort an ingest
        logger.exception("unexpected resolver error for %r", query)
        return None
    if not result.results:
        return None
    return PaperMetadata.model_validate(result.results[0].model_dump(exclude={"bibtex"}))


def _fallback_title(info: PdfInfo, original_filename: str) -> str:
    if info.title:
        return info.title[:MAX_TITLE_LEN]
    stem = Path(original_filename or "").stem.replace("_", " ").strip()
    return (stem or "Untitled")[:MAX_TITLE_LEN]


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _title_coverage(title: str, text: str) -> float:
    wanted = {w for w in _words(title) if len(w) >= MIN_TITLE_WORD_LEN}
    if not wanted:
        return 0.0
    return len(wanted & set(_words(text))) / len(wanted)


def _belongs_to_pdf(meta: PaperMetadata, info: PdfInfo) -> bool:
    """An identifier scraped from PDF text may belong to a cited reference: require the resolved title
    to show up on the first page (or match the PDF's own metadata title)."""
    return any(
        _title_coverage(meta.title, text) >= TITLE_MATCH_RATIO for text in (info.first_page_text, info.title or "")
    )


async def _resolve_metadata(
    resolver: MetadataResolver,
    info: PdfInfo,
    original_filename: str,
    identifier_hint: str | None,
    hint_strict: bool = True,
) -> PaperMetadata:
    """Precedence: explicit identifier -> identifiers found in the PDF -> PDF title/filename + internal id."""
    fallback_title = _fallback_title(info, original_filename)
    if identifier_hint:
        parsed = identifiers.parse_query(identifier_hint)
        if parsed.kind == "title" and hint_strict:
            raise IngestError(422, "identifier must be a DOI or arXiv id")
        meta = await _resolve_first(resolver, identifier_hint) if parsed.kind != "title" else None
        if meta is not None:
            return meta
        # Resolver unavailable: still honour an explicit DOI/arXiv id. A PII cannot become an identifier,
        # and a DOI seen only in a URL is too weak a signal (fall through to the PDF's own identifiers).
        if parsed.kind == "arxiv":
            return PaperMetadata(
                identifier=parsed.value, identifier_type=IdentifierType.arxiv, arxiv_id=parsed.value,
                title=fallback_title, source="pdf",
            )
        if parsed.kind == "doi" and hint_strict:
            return PaperMetadata(
                identifier=parsed.value, identifier_type=IdentifierType.doi, doi=parsed.value,
                title=fallback_title, source="pdf",
            )
    for query in (info.arxiv_id, info.doi):
        if query:
            meta = await _resolve_first(resolver, query)
            if meta is not None and _belongs_to_pdf(meta, info):
                return meta
            if meta is not None:
                logger.info("ignoring identifier %s found in PDF text: title not on first page", query)
    return PaperMetadata(
        identifier=generate_internal_identifier(), identifier_type=IdentifierType.internal,
        title=fallback_title, source="pdf",
    )


# ------------------------------------------------------------------ pipeline
async def ingest_pdf(
    db: AsyncSession,
    *,
    staged: StagedFile,
    workspace: Workspace,
    user_id: uuid.UUID,
    resolver: MetadataResolver,
    original_filename: str,
    identifier_hint: str | None = None,
    identifier_hint_strict: bool = True,
    paper_id: uuid.UUID | None = None,
    status: PaperStatus | None = None,
    redis_pool_for_enqueue: Any | None = None,
) -> Paper:
    """Validate a staged PDF, resolve metadata, place it in the library, persist and enqueue indexing.

    The staged temp file is always consumed (placed or deleted). Raises ``IngestError``.
    """
    settings = get_settings()
    placed_relative: str | None = None
    try:
        if not has_pdf_magic(staged.head):
            raise IngestError(415, "File is not a PDF")
        try:
            info = await inspect_pdf_isolated(staged.path)
        except InvalidPdf as exc:
            raise IngestError(422, f"Invalid PDF: {exc}") from None

        paper: Paper | None = None
        meta: PaperMetadata | None = None
        if paper_id is not None:
            paper = (
                await db.execute(select(Paper).where(Paper.id == paper_id, Paper.workspace_id == workspace.id))
            ).scalar_one_or_none()
            if paper is None:
                raise IngestError(404, "Not found")
        else:
            meta = await _resolve_metadata(resolver, info, original_filename, identifier_hint, identifier_hint_strict)
            paper = await _find_by_identifier(db, workspace.id, meta.identifier)
        if paper is not None and paper.has_pdf:
            raise IngestError(409, "Paper already has a PDF", paper_id=paper.id)

        if paper is not None:
            path_meta = meta_from_paper(paper)
        else:
            assert meta is not None
            path_meta = meta
        try:
            relative = build_relative_path(
                path_meta.year, path_meta.identifier, path_meta.title, settings.filename_template
            )
        except ValueError:
            raise IngestError(422, "Cannot derive a storage path for this paper") from None
        try:
            placed = await asyncio.to_thread(place_file, staged, relative, settings.library_root)
        except (PlacementError, PathTraversalError, OSError):
            logger.exception("failed to place file")
            raise IngestError(500, "Could not store file") from None
        placed_relative = placed.relative

        workspace_id = workspace.id
        existing = paper
        new_paper: Paper | None = None
        attempts = 0

        async def attempt() -> None:
            nonlocal new_paper, attempts
            attempts += 1
            target = existing
            if target is None:
                assert meta is not None
                citekey = await unique_citekey(db, workspace_id, meta)
                target = new_paper = Paper(
                    id=uuid.uuid4(),
                    workspace_id=workspace_id,
                    created_by=user_id,
                    citekey=citekey,
                    bibtex=make_bibtex(meta, citekey),
                    status=status or PaperStatus.unread,
                    **_new_paper_fields(meta),
                )
                db.add(target)
            else:
                if attempts > 1:
                    await db.refresh(target)  # the rolled-back SAVEPOINT expired it
                if status is not None:
                    target.status = status
                elif target.status == PaperStatus.wishlist:
                    target.status = PaperStatus.unread
                if not target.citekey:
                    target.citekey = await unique_citekey(db, workspace_id, path_meta, exclude_id=target.id)
                    regenerate_bibtex(target)
            target.has_pdf = True
            target.relative_file_path = placed.relative
            target.file_sha256 = staged.sha256
            target.file_size = staged.size
            target.page_count = info.page_count
            target.index_status = IndexStatus.pending
            await db.flush()

        try:
            await _flush_retrying_citekey(db, attempt)
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            raise _conflict_from(exc) from None
        paper = existing or new_paper
    except BaseException:
        discard(staged)
        if placed_relative is not None:
            # Never delete: a concurrent request may have reused this identical file (same-sha reuse).
            logger.warning("orphaned file left in library: %s", placed_relative)
        try:
            await db.rollback()
        except Exception:  # noqa: BLE001
            pass
        raise
    await queue.enqueue_process_paper(paper.id, redis_pool_for_enqueue)
    return paper


async def create_metadata_only(
    db: AsyncSession, *, workspace: Workspace, user_id: uuid.UUID, meta: PaperMetadata, tags: list[str]
) -> Paper:
    """Persist a wishlist paper without a PDF. 409 if the identifier already exists in the workspace."""
    tags = normalize_tags(tags)
    existing = await _find_by_identifier(db, workspace.id, meta.identifier)
    if existing is not None:
        raise IngestError(409, "Paper already exists", paper_id=existing.id)
    workspace_id = workspace.id
    created: Paper | None = None

    async def attempt() -> None:
        nonlocal created
        citekey = await unique_citekey(db, workspace_id, meta)
        created = Paper(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            created_by=user_id,
            citekey=citekey,
            bibtex=make_bibtex(meta, citekey),
            status=PaperStatus.wishlist,
            has_pdf=False,
            **_new_paper_fields(meta),
        )
        db.add(created)
        await db.flush()
        await set_paper_tags(db, created, tags)

    try:
        await _flush_retrying_citekey(db, attempt)
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise _conflict_from(exc) from None
    assert created is not None
    return created


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_original_name(name: str | None, default: str = "upload.pdf") -> str:
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    base = _SAFE_NAME.sub("_", base).strip(" .")[:200]
    return base or default
