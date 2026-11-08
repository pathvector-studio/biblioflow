"""Shared item model and helpers for the bibliography file parsers (no network, no database)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from app.schemas.metadata import Author
from app.schemas.paper import MAX_TAG_LEN, MAX_TAGS
from app.services.metadata import identifiers

MAX_TITLE = 1000
MAX_AUTHORS = 200
MAX_ABSTRACT = 50_000
MAX_NOTE = 100_000
MAX_NOTES_PER_ITEM = 50
MAX_COLLECTION_NAME = 200
MAX_COLLECTION_DEPTH = 12
MAX_ATTACHMENTS = 20
MAX_CITEKEY = 60

_CITEKEY = re.compile(r"^[A-Za-z][A-Za-z0-9_:.\-]*$")
_UUID_LIKE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-", re.IGNORECASE)
_WEB_URL = re.compile(r"^https?://", re.IGNORECASE)
_VON = {"van", "von", "de", "der", "den", "del", "della", "di", "da", "du", "la", "le", "ten", "ter", "bin", "al", "dos", "das"}


class ImportFormatError(ValueError):
    """The whole file cannot be read (unknown format, malformed JSON / XML, no entries)."""


@dataclass
class ImportItem:
    """One bibliographic record read from a file.

    ``attachments`` are file paths exactly as written in the file (unvalidated; web URLs are dropped). Nothing
    in this package opens them: a later step (server-folder import) must confine them to its own directory.
    ``collections`` are paths from the root (``["Networking", "BGP"]``).
    """

    ref: str  # label for messages: citekey, or "entry 3"
    title: str
    authors: list[Author] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    abstract: str | None = None
    citekey: str | None = None  # hint from the file; the importer keeps it only if it is free
    bibtex: str | None = None  # the original BibTeX entry text, for BibTeX imports
    tags: list[str] = field(default_factory=list)
    collections: list[list[str]] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    attachments: list[str] = field(default_factory=list)
    item_type: str | None = None


@dataclass
class ParseProblem:
    ref: str
    reason: str


@dataclass
class ParseResult:
    format: str
    items: list[ImportItem] = field(default_factory=list)
    problems: list[ParseProblem] = field(default_factory=list)


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip()


def clean_block(text: str | None) -> str:
    """Trim and collapse runs of blank lines but keep line breaks (abstracts, notes)."""
    lines = [ln.rstrip() for ln in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def unique_strings(values: list[str], limit: int | None = None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for v in values:
        if v and v.casefold() not in seen:
            seen.add(v.casefold())
            out.append(v)
    return out[:limit] if limit else out


def split_keywords(value: str | None) -> list[str]:
    """``"bgp; routing, clos"`` -> three tags (comma, semicolon or newline separated)."""
    return [clean(p) for p in re.split(r"[;,\n]", value or "") if clean(p)]


def parse_person(name: str) -> Author | None:
    """``"Smith, Alice"`` / ``"Alice B. Smith"`` / ``"{World Health Organization}"`` -> Author."""
    name = clean(name)
    if not name:
        return None
    if name.startswith("{") and name.endswith("}"):
        return _author(name[1:-1], None)
    if "," in name:
        parts = [clean(p) for p in name.split(",")]
        family, given = parts[0], parts[-1] if len(parts) > 1 else ""
        return _author(family, given or None)
    tokens = name.split(" ")
    if len(tokens) == 1:
        return _author(tokens[0], None)
    i = len(tokens) - 1
    while i > 1 and tokens[i - 1].lower() in _VON:  # "Ludwig van Beethoven": family "van Beethoven"
        i -= 1
    return _author(" ".join(tokens[i:]), " ".join(tokens[:i]) or None)


def _author(family: str, given: str | None) -> Author | None:
    family = clean(family)[:200]
    if not family:
        return None
    return Author(family=family, given=(clean(given)[:200] or None) if given else None)


def parse_year(value: object) -> int | None:
    m = re.search(r"(?<!\d)(1[0-9]{3}|20[0-9]{2}|2100)(?!\d)", str(value or ""))
    return int(m.group(1)) if m else None


def extract_ids(*texts: str | None) -> tuple[str | None, str | None]:
    """``(doi, arxiv_id)`` from DOI strings, ``arXiv:`` stamps and arxiv.org / doi.org URLs.

    The values are only parsed; nothing is fetched."""
    doi: str | None = None
    arxiv: str | None = None
    for text in texts:
        t = clean(text)
        if not t:
            continue
        if _WEB_URL.match(t):
            parsed = identifiers.parse_query(t)
            if parsed.kind == "doi":
                doi = doi or parsed.value
            elif parsed.kind == "arxiv":
                arxiv = arxiv or parsed.value
            continue
        d, a = identifiers.find_identifiers_in_text(t)
        doi = doi or d
        arxiv = arxiv or a
        if not (d or a):
            parsed = identifiers.parse_query(t)  # a bare "2405.12345" in an eprint-like field
            if parsed.kind == "arxiv":
                arxiv = arxiv or parsed.value
    return doi, arxiv


def citekey_hint(value: str | None) -> str | None:
    v = clean(value)
    if not v or len(v) > MAX_CITEKEY or not _CITEKEY.match(v) or _UUID_LIKE.match(v):
        return None
    return v


def local_attachment_paths(values: list[str]) -> list[str]:
    """Candidates that look like file paths: drops empty values and http(s) URLs."""
    out = []
    for v in values:
        v = (v or "").strip()
        if v and "\x00" not in v and not _WEB_URL.match(v) and len(v) <= 1000:
            out.append(v)
    return unique_strings(out, MAX_ATTACHMENTS)


def make_item(
    *,
    ref: str,
    title: str | None,
    authors: list[Author] | None = None,
    year: int | None = None,
    venue: str | None = None,
    doi: str | None = None,
    arxiv_id: str | None = None,
    abstract: str | None = None,
    citekey: str | None = None,
    bibtex: str | None = None,
    tags: list[str] | None = None,
    collections: list[list[str]] | None = None,
    notes: list[str] | None = None,
    attachments: list[str] | None = None,
    item_type: str | None = None,
) -> ImportItem | ParseProblem:
    """Validate and cap a raw record. Returns a ``ParseProblem`` if it has no usable title."""
    title = clean(title)
    if not title:
        return ParseProblem(ref, "missing title")
    tag_list = unique_strings([clean(t)[:MAX_TAG_LEN] for t in (tags or []) if clean(t)], MAX_TAGS)
    notes_list = [n for n in (clean_block(n)[:MAX_NOTE] for n in (notes or [])) if n][:MAX_NOTES_PER_ITEM]
    paths: list[list[str]] = []
    for path in collections or []:
        names = [clean(n)[:MAX_COLLECTION_NAME] for n in path if clean(n)][:MAX_COLLECTION_DEPTH]
        if names and names not in paths:
            paths.append(names)
    return ImportItem(
        ref=ref,
        title=title[:MAX_TITLE],
        authors=(authors or [])[:MAX_AUTHORS],
        year=year if year and 1 <= year <= 9999 else None,
        venue=clean(venue)[:1000] or None,
        doi=doi.lower() if doi else None,
        arxiv_id=arxiv_id or None,
        abstract=clean_block(abstract)[:MAX_ABSTRACT] or None,
        citekey=citekey_hint(citekey),
        bibtex=bibtex,
        tags=tag_list,
        collections=paths,
        notes=notes_list,
        attachments=local_attachment_paths(attachments or []),
        item_type=clean(item_type)[:50] or None,
    )
