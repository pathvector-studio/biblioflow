"""Normalization of user input into arXiv id / DOI / PII / title queries."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, unquote, urlsplit

_ARXIV_NEW = r"\d{4}\.\d{4,5}"
_ARXIV_OLD = r"[a-z\-]+(?:\.[A-Za-z]{2})?/\d{7}"
_ARXIV_ID = rf"(?:{_ARXIV_NEW}|{_ARXIV_OLD})"
_ARXIV_FULL = re.compile(rf"^({_ARXIV_ID})(?:v\d+)?$", re.IGNORECASE)
_ARXIV_STAMP = re.compile(rf"arxiv\s*:\s*({_ARXIV_ID})(?:v\d+)?", re.IGNORECASE)
_DOI_FULL = re.compile(r"^10\.\d{4,9}/\S+$")
_DOI_PREFIXED = re.compile(r"(?:\bdoi\s*:?\s*|doi\.org/)(10\.\d{4,9}/[^\s\"<>]+)", re.IGNORECASE)
_DOI_BARE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>]+)")
_PII = r"S\d{4}-?\d{3}[\dX]\(?\d{2}\)?\d{5}-?[\dX]"
_PII_FULL = re.compile(rf"^{_PII}$", re.IGNORECASE)
_PII_SEARCH = re.compile(_PII, re.IGNORECASE)
_ARXIV_DOI_PREFIX = "10.48550/arxiv."
_TRAILING = ".,;:)]}>\"'"
_DOI_HOSTS = {"doi.org", "dx.doi.org", "www.doi.org"}


@dataclass(frozen=True)
class ParsedQuery:
    kind: Literal["arxiv", "doi", "pii", "title"]
    value: str


def _norm_doi(doi: str) -> str:
    return doi.strip().rstrip(_TRAILING).lower()


def _norm_pii(pii: str) -> str:
    return re.sub(r"[-()]", "", pii).upper()


def _norm_arxiv(raw: str) -> str:
    m = _ARXIV_FULL.match(raw.strip())
    return m.group(1) if m else raw.strip()


def _doi_result(doi: str) -> ParsedQuery:
    doi = _norm_doi(doi)
    if doi.startswith(_ARXIV_DOI_PREFIX):
        aid = _ARXIV_FULL.match(doi[len(_ARXIV_DOI_PREFIX) :])
        if aid:
            return ParsedQuery("arxiv", _norm_arxiv(aid.group(1)))
    return ParsedQuery("doi", doi)


def _parse_url(s: str) -> ParsedQuery | None:
    try:
        u = urlsplit(s)
    except ValueError:
        return None
    host = (u.hostname or "").lower()
    path = unquote(u.path)
    # Signed asset URLs: only the PII is retained, never the rest of the URL.
    for val in parse_qs(u.query).get("pii", []):
        if _PII_FULL.match(val):
            return ParsedQuery("pii", _norm_pii(val))
    if any(host == d or host.endswith("." + d) for d in ("sciencedirect.com", "sciencedirectassets.com")):
        m = re.search(rf"/pii/({_PII})", path, re.IGNORECASE)
        if m:
            return ParsedQuery("pii", _norm_pii(m.group(1)))
    if host in ("arxiv.org", "www.arxiv.org", "export.arxiv.org"):
        m = re.match(r"^/(?:abs|pdf)/(.+?)(?:\.pdf)?/?$", path)
        if m:
            fm = _ARXIV_FULL.match(m.group(1))
            if fm:
                return ParsedQuery("arxiv", _norm_arxiv(fm.group(1)))
    if host in _DOI_HOSTS:
        cand = path.lstrip("/")
        if _DOI_FULL.match(cand):
            return _doi_result(cand)
    return None


def parse_query(q: str) -> ParsedQuery:
    s = q.strip()
    if re.match(r"^https?://", s, re.IGNORECASE):
        parsed = _parse_url(s)
        if parsed:
            return parsed
        return ParsedQuery("title", s)
    if _PII_FULL.match(s):
        return ParsedQuery("pii", _norm_pii(s))
    low = s.lower()
    if low.startswith("arxiv:"):
        s = s[6:].strip()
    elif low.startswith("doi:"):
        s = s[4:].strip()
    m = _ARXIV_FULL.match(s)
    if m:
        return ParsedQuery("arxiv", _norm_arxiv(m.group(1)))
    if _DOI_FULL.match(s):
        return _doi_result(s)
    return ParsedQuery("title", q.strip())


# ScienceDirect's download name: "<title> - 1-s2.0-S0142061525003874-main.pdf" (also kept as the PDF title
# by "print to PDF").
_SD_DOWNLOAD = re.compile(rf"\s*(?:[-\u2013\u2014_]\s*)?1-s2\.0-({_PII})(?:-main)?(?:\.pdf)?\s*$", re.IGNORECASE)
_PII_IN_TEXT = re.compile(rf"(?<![A-Za-z0-9]){_PII}(?![0-9])", re.IGNORECASE)
_LETTER_SPACED_MIN_TOKENS = 6
_LETTER_SPACED_RATIO = 0.6


def strip_download_suffix(title: str) -> str:
    """Drop a trailing ScienceDirect download suffix (``" - 1-s2.0-<PII>-main.pdf"``) from a title or file name.

    Anything else is returned unchanged (a plain ``foo.pdf`` title stays junk for the caller to reject)."""
    return _SD_DOWNLOAD.sub("", title).strip() if _SD_DOWNLOAD.search(title) else title


def find_pii(text: str) -> str | None:
    """A ScienceDirect PII in a download name (``1-s2.0-<PII>-main``) or in free text, normalized."""
    m = _SD_DOWNLOAD.search(text) or _PII_IN_TEXT.search(text)
    return _norm_pii(m.group(1) if m.re is _SD_DOWNLOAD else m.group(0)) if m else None


def compact_letter_spacing(text: str) -> str:
    """Join lines that a PDF text layer spells out letter by letter ("C o n t e n t s  l i s t s").

    Browsers' "print to PDF" often emits one glyph per text run. Word boundaries are lost on those lines,
    which is fine for identifier search and substring title checks.
    """
    out = []
    for line in text.splitlines():
        tokens = [t for t in line.split(" ") if t]
        if len(tokens) >= _LETTER_SPACED_MIN_TOKENS and sum(len(t) == 1 for t in tokens) / len(tokens) >= _LETTER_SPACED_RATIO:
            line = "".join(tokens)
        out.append(line)
    return "\n".join(out)


def find_identifiers_in_text(text: str) -> tuple[str | None, str | None]:
    """Return ``(doi, arxiv_id)`` found in free text such as the first pages of a PDF."""
    arxiv_id = None
    m = _ARXIV_STAMP.search(text)
    if m:
        arxiv_id = _norm_arxiv(m.group(1))
    doi = None
    m = _DOI_PREFIXED.search(text) or _DOI_BARE.search(text)
    if m:
        doi = _norm_doi(m.group(1))
        if doi.startswith(_ARXIV_DOI_PREFIX):
            if arxiv_id is None:
                am = _ARXIV_FULL.match(doi[len(_ARXIV_DOI_PREFIX) :])
                arxiv_id = _norm_arxiv(am.group(1)) if am else None
            doi = None
    return doi, arxiv_id
