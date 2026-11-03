"""Deterministic BibTeX / citekey generation (no network)."""

from __future__ import annotations

import re
import unicodedata

from app.models.paper import IdentifierType
from app.schemas.metadata import Author, PaperMetadata

_STOPWORDS = frozenset({"a", "an", "the", "on", "of", "in", "for", "to", "and", "with", "towards"})
_LATEX = {
    "\\": r"\textbackslash{}",
    "{": r"\{",
    "}": r"\}",
    "%": r"\%",
    "&": r"\&",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "^": r"\textasciicircum{}",
    "~": r"\textasciitilde{}",
}
_LATEX_RE = re.compile("[" + re.escape("".join(_LATEX)) + "]")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


def _ascii(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower()


def citekey_base(meta: PaperMetadata) -> str:
    author = re.sub(r"[^a-z0-9]", "", _ascii(meta.authors[0].family)) if meta.authors else ""
    year = str(meta.year) if meta.year else "nd"
    word = ""
    for w in re.findall(r"[a-z0-9]+", _ascii(meta.title)):
        if w not in _STOPWORDS:
            word = w
            break
    key = re.sub(r"[^a-z0-9]", "", f"{author}{year}{word}")[:60]
    return key if key and key != "nd" else "paper"


def _esc(value: str) -> str:
    value = _CONTROL_RE.sub(" ", value)
    return _LATEX_RE.sub(lambda m: _LATEX[m.group(0)], value).strip()


def _author(a: Author) -> str:
    return f"{a.family}, {a.given}" if a.given else a.family


def make_bibtex(meta: PaperMetadata, citekey: str) -> str:
    key = re.sub(r"[^A-Za-z0-9_:.\-]", "", citekey) or "paper"
    is_arxiv = meta.identifier_type == IdentifierType.arxiv
    fields: list[tuple[str, str]] = [("title", meta.title)]
    if meta.authors:
        fields.append(("author", " and ".join(_author(a) for a in meta.authors)))
    if meta.year:
        fields.append(("year", str(meta.year)))
    if is_arxiv:
        entry = "misc"
        aid = meta.arxiv_id or meta.identifier
        fields += [("eprint", aid), ("archivePrefix", "arXiv"), ("url", f"https://arxiv.org/abs/{aid}")]
        if meta.doi:
            fields.append(("doi", meta.doi))
    else:
        entry = "article" if meta.venue else "misc"
        if meta.venue:
            fields.append(("journal", meta.venue))
        if meta.doi:
            fields += [("doi", meta.doi), ("url", f"https://doi.org/{meta.doi}")]
    body = ",\n".join(f"  {k} = {{{_esc(v)}}}" for k, v in fields)
    return f"@{entry}{{{key},\n{body}\n}}\n"
