"""Apply parsed bibliography records to a workspace (file import, metadata only).

Semantics (spec R7):
- A record whose DOI, arXiv id or identifier equals an existing paper's in the same workspace (or an earlier
  record of the same file) is *matched*: its tags and collections are added and its notes appended; no stored
  value (title, authors, ...) is overwritten.
- Any other record becomes a ``wishlist`` paper without a PDF. The file is the only source: no external
  metadata API is called and no URL in the file is opened. A record without DOI / arXiv id gets a deterministic
  ``BF-`` identifier derived from title, year and first author, so importing the same file again matches it.
- One transaction per request. Each record runs in its own SAVEPOINT: a record that fails to save is reported as
  unreadable and the others continue. ``dry_run`` goes through the same code without writing anything, so the
  preview counts are exactly what the commit does.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.collection import Collection, paper_collections
from app.models.paper import IdentifierType, Paper
from app.models.tag import Tag, paper_tags
from app.models.workspace import Workspace
from app.schemas.metadata import PaperMetadata
from app.schemas.paper import MAX_TAGS
from app.services import ingest
from app.services.importers import ImportItem, ParseResult

logger = logging.getLogger("biblioflow.import")

MAX_NOTES_CHARS = 500_000
NOTE_SEPARATOR = "\n\n---\n\n"
_QUERY_CHUNK = 1000


@dataclass
class ItemResult:
    ref: str
    title: str
    status: str  # "new" | "matched" | "unreadable"
    identifier: str | None = None
    matched_by: str | None = None  # "existing" | "file"
    reason: str | None = None
    paper_id: uuid.UUID | None = None  # set by a real run (not a dry run) for records that were saved or matched
    attachments: list[str] = field(default_factory=list)  # paths as written in the file (unvalidated)


@dataclass
class ImportOutcome:
    format: str
    dry_run: bool
    results: list[ItemResult] = field(default_factory=list)
    with_attachments: int = 0
    tags_added: int = 0
    collections_created: int = 0
    collection_links: int = 0
    notes_added: int = 0

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)


def internal_identifier(item: ImportItem) -> str:
    """Deterministic ``BF-`` id so the same title / year / first author is the same paper on every import."""
    words = " ".join(re.findall(r"\w+", item.title.casefold()))
    first = item.authors[0].family.casefold() if item.authors else ""
    digest = hashlib.sha256(f"{words}|{item.year or ''}|{first}".encode()).digest()
    return "BF-" + base64.b32encode(digest).decode("ascii")[:10]


def to_metadata(item: ImportItem) -> PaperMetadata:
    """arXiv > DOI > internal, as for every other way of adding a paper."""
    if item.arxiv_id:
        ident, kind = item.arxiv_id, IdentifierType.arxiv
    elif item.doi:
        ident, kind = item.doi, IdentifierType.doi
    else:
        ident, kind = internal_identifier(item), IdentifierType.internal
    return PaperMetadata(
        identifier=ident, identifier_type=kind, doi=item.doi, arxiv_id=item.arxiv_id, title=item.title,
        authors=item.authors, year=item.year, venue=item.venue, abstract=item.abstract, source="manual",
    )


def _keys(meta: PaperMetadata) -> list[str]:
    return [k.lower() for k in dict.fromkeys(x for x in (meta.arxiv_id, meta.doi, meta.identifier) if x)]


def _chunks(values: list[str]):
    for i in range(0, len(values), _QUERY_CHUNK):
        yield values[i : i + _QUERY_CHUNK]


async def _existing_by_key(db: AsyncSession, workspace_id: uuid.UUID, metas: list[PaperMetadata]) -> dict[str, uuid.UUID]:
    """Lower-cased DOI / arXiv id / identifier -> id of the oldest paper in the workspace that has it."""
    keys = sorted({k for m in metas for k in _keys(m)})
    idents = sorted({m.identifier for m in metas} | set(keys))
    cols = (Paper.id, Paper.doi, Paper.arxiv_id, Paper.identifier, Paper.created_at)
    rows: dict[uuid.UUID, tuple] = {}
    for chunk in _chunks(keys):
        q = select(*cols).where(Paper.workspace_id == workspace_id, Paper.doi.in_(chunk) | Paper.arxiv_id.in_(chunk))
        rows.update({r[0]: tuple(r) for r in (await db.execute(q)).all()})
    for chunk in _chunks(idents):
        q = select(*cols).where(Paper.workspace_id == workspace_id, Paper.identifier.in_(chunk))
        rows.update({r[0]: tuple(r) for r in (await db.execute(q)).all()})
    by_key: dict[str, uuid.UUID] = {}
    for pid, doi, arxiv, ident, _created in sorted(rows.values(), key=lambda r: r[4]):
        for k in (arxiv, doi, ident):
            if k:
                by_key.setdefault(k.lower(), pid)
    return by_key


class _Context:
    """Per-request caches. Entries learnt inside a SAVEPOINT only become visible once it has succeeded."""

    def __init__(self, workspace_id: uuid.UUID, user_id: uuid.UUID) -> None:
        self.workspace_id = workspace_id
        self.user_id = user_id
        self.tag_ids: dict[str, uuid.UUID] = {}
        self.collections: dict[tuple[uuid.UUID | None, str], uuid.UUID] = {}
        self.citekeys: set[str] = set()
        self._tags_pending: dict[str, uuid.UUID] = {}
        self._cols_pending: dict[tuple[uuid.UUID | None, str], uuid.UUID] = {}
        self._citekeys_pending: set[str] = set()
        self.totals = {"tags": 0, "collections": 0, "links": 0, "notes": 0}
        self._pending_totals = dict.fromkeys(self.totals, 0)

    def bump(self, name: str, n: int) -> None:
        self._pending_totals[name] += n

    def settle(self) -> None:
        self.tag_ids.update(self._tags_pending)
        self.collections.update(self._cols_pending)
        self.citekeys |= self._citekeys_pending
        for k, v in self._pending_totals.items():
            self.totals[k] += v
        self.discard()

    def discard(self) -> None:
        self._tags_pending, self._cols_pending, self._citekeys_pending = {}, {}, set()
        self._pending_totals = dict.fromkeys(self.totals, 0)


async def _load_context(db: AsyncSession, workspace_id: uuid.UUID, user_id: uuid.UUID) -> _Context:
    ctx = _Context(workspace_id, user_id)
    for tid, name in (await db.execute(select(Tag.id, Tag.name).where(Tag.workspace_id == workspace_id))).all():
        ctx.tag_ids.setdefault(name.casefold(), tid)
    rows = await db.execute(
        select(Collection.id, Collection.parent_id, Collection.name).where(Collection.workspace_id == workspace_id)
    )
    for cid, parent, name in rows.all():
        ctx.collections.setdefault((parent, name.casefold()), cid)
    ctx.citekeys = {c for (c,) in (await db.execute(select(Paper.citekey).where(Paper.workspace_id == workspace_id))).all() if c}
    return ctx


async def _add_tags(db: AsyncSession, ctx: _Context, paper_id: uuid.UUID, names: list[str], *, is_new: bool) -> None:
    if not names:
        return
    have: set[uuid.UUID] = set()
    if not is_new:
        have = {t for (t,) in (await db.execute(select(paper_tags.c.tag_id).where(paper_tags.c.paper_id == paper_id))).all()}
    room = MAX_TAGS - len(have)
    wanted: list[tuple[str, uuid.UUID]] = []
    for name in names:
        key = name.casefold()
        tid = ctx.tag_ids.get(key) or ctx._tags_pending.get(key)
        if tid is None:
            tid = uuid.uuid4()
            await db.execute(pg_insert(Tag).values(id=tid, workspace_id=ctx.workspace_id, name=name).on_conflict_do_nothing())
            # A concurrent request may have created it: read back whichever row won.
            tid = (
                await db.execute(select(Tag.id).where(Tag.workspace_id == ctx.workspace_id, Tag.name == name))
            ).scalar_one()
            ctx._tags_pending[key] = tid
        if tid not in have:
            have.add(tid)
            wanted.append((name, tid))
    wanted = wanted[: max(room, 0)]
    if wanted:
        res = await db.execute(
            pg_insert(paper_tags).values([{"paper_id": paper_id, "tag_id": t} for _, t in wanted]).on_conflict_do_nothing()
        )
        ctx.bump("tags", res.rowcount or 0)


async def _ensure_collection(db: AsyncSession, ctx: _Context, path: list[str]) -> uuid.UUID:
    parent: uuid.UUID | None = None
    for name in path:
        key = (parent, name.casefold())
        cid = ctx.collections.get(key) or ctx._cols_pending.get(key)
        if cid is None:
            cid = uuid.uuid4()
            db.add(Collection(id=cid, workspace_id=ctx.workspace_id, name=name, parent_id=parent))
            await db.flush()
            ctx._cols_pending[key] = cid
            ctx.bump("collections", 1)
        parent = cid
    assert parent is not None
    return parent


async def _add_collections(db: AsyncSession, ctx: _Context, paper_id: uuid.UUID, paths: list[list[str]]) -> None:
    for path in paths:
        cid = await _ensure_collection(db, ctx, path)
        res = await db.execute(
            pg_insert(paper_collections).values(paper_id=paper_id, collection_id=cid).on_conflict_do_nothing()
        )
        ctx.bump("links", res.rowcount or 0)


def _join_notes(notes: list[str]) -> str:
    return NOTE_SEPARATOR.join(notes)


async def _append_notes(db: AsyncSession, ctx: _Context, paper_id: uuid.UUID, notes: list[str]) -> None:
    """Append notes that are not already in ``notes_md`` (so importing twice does not repeat them)."""
    if not notes:
        return
    current = (await db.execute(select(Paper.notes_md).where(Paper.id == paper_id))).scalar_one()
    fresh = [n for n in notes if n not in current]
    if not fresh:
        return
    addition = _join_notes(fresh)
    new_value = f"{current}{NOTE_SEPARATOR}{addition}" if current.strip() else addition
    if len(new_value) > MAX_NOTES_CHARS:
        raise ValueError("notes would exceed the size limit")
    await db.execute(update(Paper).where(Paper.id == paper_id).values(notes_md=new_value))
    ctx.bump("notes", len(fresh))


async def _process_item(
    db: AsyncSession, ctx: _Context, ws: Workspace, item: ImportItem, meta: PaperMetadata, existing: uuid.UUID | None
) -> uuid.UUID:
    if existing is not None:
        await _add_tags(db, ctx, existing, item.tags, is_new=False)
        await _add_collections(db, ctx, existing, item.collections)
        await _append_notes(db, ctx, existing, item.notes)
        return existing
    hint = item.citekey if item.citekey and item.citekey not in ctx.citekeys else None
    paper = await ingest.stage_wishlist_paper(
        db, workspace_id=ws.id, user_id=ctx.user_id, meta=meta, citekey=hint, bibtex=item.bibtex,
        notes_md=_join_notes(item.notes)[:MAX_NOTES_CHARS],
    )
    pid, citekey = paper.id, paper.citekey
    ctx.bump("notes", len(item.notes))
    await _add_tags(db, ctx, pid, item.tags, is_new=True)
    await _add_collections(db, ctx, pid, item.collections)
    if citekey:
        ctx._citekeys_pending.add(citekey)
    return pid


def _check(item: ImportItem, meta: PaperMetadata) -> str | None:
    if len(meta.doi or "") > 255 or len(meta.arxiv_id or "") > 64:
        return "identifier too long"
    return None


async def run_import(
    db: AsyncSession, *, workspace: Workspace, user_id: uuid.UUID, parsed: ParseResult, dry_run: bool
) -> ImportOutcome:
    """Classify (and, unless ``dry_run``, import) every record. The caller commits."""
    out = ImportOutcome(format=parsed.format, dry_run=dry_run)
    for p in parsed.problems:
        out.results.append(ItemResult(ref=p.ref, title=p.ref, status="unreadable", reason=p.reason))
    metas = [to_metadata(i) for i in parsed.items]
    # key -> paper id; None = created earlier in this file during a dry run (nothing exists to point at)
    known: dict[str, uuid.UUID | None] = dict(await _existing_by_key(db, workspace.id, metas))
    in_file: set[str] = set()
    ctx = None if dry_run else await _load_context(db, workspace.id, user_id)

    for item, meta in zip(parsed.items, metas):
        reason = _check(item, meta)
        if reason is not None:
            out.results.append(ItemResult(ref=item.ref, title=item.title, status="unreadable", reason=reason))
            continue
        keys = _keys(meta)
        hit = next((k for k in keys if k in known), None)
        existing = known[hit] if hit is not None else None
        result = ItemResult(
            ref=item.ref, title=item.title, status="matched" if hit else "new", identifier=meta.identifier,
            matched_by=("file" if hit in in_file else "existing") if hit else None, attachments=item.attachments,
        )
        if item.attachments:
            out.with_attachments += 1
        pid: uuid.UUID | None = existing
        if ctx is not None:
            try:
                async with db.begin_nested():
                    pid = await _process_item(db, ctx, workspace, item, meta, existing)
                ctx.settle()
            except (SQLAlchemyError, ValueError) as exc:
                ctx.discard()
                logger.warning("import of %r failed: %s", item.ref, type(exc).__name__)
                result.status, result.matched_by = "unreadable", None
                result.reason = str(exc) if isinstance(exc, ValueError) else "could not be saved"
                out.results.append(result)
                continue
        if ctx is not None:
            result.paper_id = pid
        if not hit:
            in_file.update(keys)
            for k in keys:
                known[k] = pid
        out.results.append(result)

    if ctx is not None:
        t = ctx.totals
        out.tags_added, out.collections_created = t["tags"], t["collections"]
        out.collection_links, out.notes_added = t["links"], t["notes"]
    return out
