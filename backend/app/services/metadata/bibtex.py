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
# BibTeX counts braces even after a backslash, so "\}" alone still closes the field.
_LOOSE_BRACES = {"{": r"\textbraceleft{}", "}": r"\textbraceright{}"}
_NEEDS_BRACES = re.compile(r"\sand\s|,", re.IGNORECASE)


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


def _nested(value: str) -> bool:
    depth = 0
    for ch in value:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                return False
    return depth == 0


def _esc(value: str) -> str:
    value = _CONTROL_RE.sub(" ", value)
    table = _LATEX if _nested(value) else {**_LATEX, **_LOOSE_BRACES}
    return _LATEX_RE.sub(lambda m: table[m.group(0)], value).strip()


def _verbatim(value: str) -> str:
    """DOI / URL / eprint: read verbatim by biblatex and ``\\url``, so no LaTeX escapes (``10.1007/978-3_13``)."""
    value = _CONTROL_RE.sub("", value).strip()
    return value if _nested(value) else value.replace("{", "").replace("}", "")


def _name_part(value: str, *, group: bool) -> str:
    """One part of a name. Braces keep ``and`` / commas inside it, and keep a multi-word name in one piece."""
    out = _esc(value)
    return f"{{{out}}}" if out and (group or _NEEDS_BRACES.search(value)) else out


def _author(a: Author) -> str:
    if not a.given:  # an institution or a single name: "World Health Organization" is not "Organization, World Health"
        return _name_part(a.family, group=" " in a.family.strip())
    return f"{_name_part(a.family, group=False)}, {_name_part(a.given, group=False)}"


def make_bibtex(meta: PaperMetadata, citekey: str) -> str:
    key = re.sub(r"[^A-Za-z0-9_:.\-]", "", citekey) or "paper"
    is_arxiv = meta.identifier_type == IdentifierType.arxiv
    fields: list[tuple[str, str]] = [("title", _esc(meta.title))]  # (name, rendered value)
    authors = [x for x in (_author(a) for a in meta.authors) if x]
    if authors:
        fields.append(("author", " and ".join(authors)))
    if meta.year:
        fields.append(("year", str(meta.year)))
    if is_arxiv:
        entry = "misc"
        aid = _verbatim(meta.arxiv_id or meta.identifier)
        fields += [("eprint", aid), ("archivePrefix", "arXiv"), ("url", f"https://arxiv.org/abs/{aid}")]
        if meta.doi:
            fields.append(("doi", _verbatim(meta.doi)))
    else:
        entry = "article" if meta.venue else "misc"
        if meta.venue:
            fields.append(("journal", _esc(meta.venue)))
        if meta.doi:
            doi = _verbatim(meta.doi)
            fields += [("doi", doi), ("url", f"https://doi.org/{doi}")]
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields)
    return f"@{entry}{{{key},\n{body}\n}}\n"
