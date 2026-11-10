"""Bulk export of bibliographic records as BibTeX, RIS and CSL JSON (stdlib only, no network)."""

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from app.models.paper import IdentifierType
from app.schemas.metadata import Author, PaperMetadata
from app.services.metadata.bibtex import citekey_base, make_bibtex

FORMATS = {
    "bibtex": ("application/x-bibtex", "bib"),
    "ris": ("application/x-research-info-systems", "ris"),
    "csljson": ("application/vnd.citationstyles.csl+json", "json"),
}

_ENTRY_HEAD = re.compile(r"(@(?!string\b)[A-Za-z]+\s*\{)[^,\s{}]*,", re.IGNORECASE)  # not an @string definition
_KEY_CLEAN = re.compile(r"[^A-Za-z0-9_:.\-]")


@dataclass
class ExportRecord:
    identifier: str
    identifier_type: IdentifierType
    title: str
    authors: list[Author] = field(default_factory=list)
    citekey: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    year: int | None = None
    venue: str | None = None
    abstract: str | None = None
    bibtex: str | None = None


def record_from_paper(p) -> ExportRecord:
    return ExportRecord(
        identifier=p.identifier,
        identifier_type=p.identifier_type,
        title=p.title,
        authors=[Author.model_validate(a) for a in (p.authors or [])],
        citekey=p.citekey,
        doi=p.doi,
        arxiv_id=p.arxiv_id,
        year=p.year,
        venue=p.venue,
        abstract=p.abstract,
        bibtex=p.bibtex,
    )


def _meta(r: ExportRecord) -> PaperMetadata:
    return PaperMetadata(
        identifier=r.identifier, identifier_type=r.identifier_type, doi=r.doi, arxiv_id=r.arxiv_id,
        title=r.title, authors=r.authors, year=r.year, venue=r.venue, abstract=r.abstract, source="manual",
    )


def _suffix(n: int) -> str:
    s = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        s = chr(ord("a") + rem) + s
    return s


def unique_keys(records: Sequence[ExportRecord]) -> list[str]:
    """One citekey per record, unique within the list (collisions get a, b, ... appended; the first keeps its key)."""
    used: set[str] = set()
    out: list[str] = []
    for r in records:
        base = _KEY_CLEAN.sub("", r.citekey or "") or citekey_base(_meta(r))
        key, n = base, 0
        while key in used:
            n += 1
            key = base + _suffix(n)
        used.add(key)
        out.append(key)
    return out


def to_bibtex(records: Sequence[ExportRecord]) -> str:
    entries: list[str] = []
    for r, key in zip(records, unique_keys(records)):
        stored = (r.bibtex or "").strip()
        if stored and _ENTRY_HEAD.search(stored):
            entries.append(_ENTRY_HEAD.sub(lambda m: f"{m.group(1)}{key},", stored, count=1) + "\n")
        else:
            entries.append(make_bibtex(_meta(r), key))
    return "\n".join(entries)


# ------------------------------------------------------------------ RIS
def _ris_line(tag: str, value: str) -> str:
    return f"{tag}  - {' '.join(value.split())}\r\n"


def _arxiv_id(r: ExportRecord) -> str | None:
    return r.arxiv_id or (r.identifier if r.identifier_type == IdentifierType.arxiv else None)


def to_ris(records: Sequence[ExportRecord]) -> str:
    out: list[str] = []
    for r, key in zip(records, unique_keys(records)):
        is_arxiv = r.identifier_type == IdentifierType.arxiv
        out.append(_ris_line("TY", "JOUR" if r.venue and not is_arxiv else "GEN"))
        out.append(_ris_line("ID", key))
        out.append(_ris_line("TI", r.title))
        for a in r.authors:
            out.append(_ris_line("AU", f"{a.family}, {a.given}" if a.given else a.family))
        if r.year:
            out.append(_ris_line("PY", str(r.year)))
        if r.venue:
            out.append(_ris_line("JO", r.venue))
        if r.doi:
            out.append(_ris_line("DO", r.doi))
        aid = _arxiv_id(r)
        if aid:
            out.append(_ris_line("UR", f"https://arxiv.org/abs/{aid}"))
        elif r.doi:
            out.append(_ris_line("UR", f"https://doi.org/{r.doi}"))
        if r.abstract and r.abstract.strip():
            out.append(_ris_line("AB", r.abstract))
        out.append("ER  - \r\n\r\n")
    return "".join(out)


# ------------------------------------------------------------------ CSL JSON
def to_csl_items(records: Sequence[ExportRecord]) -> list[dict]:
    items: list[dict] = []
    for r, key in zip(records, unique_keys(records)):
        is_arxiv = r.identifier_type == IdentifierType.arxiv
        item: dict = {
            "id": key,
            "type": "article-journal" if r.venue and not is_arxiv else "article",
            "title": r.title,
            "author": [{"family": a.family, **({"given": a.given} if a.given else {})} for a in r.authors],
        }
        if r.year:
            item["issued"] = {"date-parts": [[r.year]]}
        if r.venue:
            item["container-title"] = r.venue
        if r.doi:
            item["DOI"] = r.doi
        aid = _arxiv_id(r)
        if aid:
            item["URL"] = f"https://arxiv.org/abs/{aid}"
            item["number"] = aid
            item["archive"] = "arXiv"
        elif r.doi:
            item["URL"] = f"https://doi.org/{r.doi}"
        if r.abstract and r.abstract.strip():
            item["abstract"] = r.abstract
        items.append(item)
    return items


def to_csl_json(records: Sequence[ExportRecord]) -> str:
    return json.dumps(to_csl_items(records), ensure_ascii=False, indent=2) + "\n"


def render(fmt: str, records: Sequence[ExportRecord]) -> str:
    if fmt == "bibtex":
        return to_bibtex(records)
    if fmt == "ris":
        return to_ris(records)
    if fmt == "csljson":
        return to_csl_json(records)
    raise ValueError(fmt)
