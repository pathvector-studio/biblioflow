"""BibTeX reader.

The file is cut into entries with a small brace-aware scanner (so one broken entry cannot swallow the rest and
the original text of each entry can be kept), and each entry is parsed on its own by ``bibtexparser``.
"""

from __future__ import annotations

import re

import bibtexparser
from bibtexparser.bparser import BibTexParser
from bibtexparser.latexenc import latex_to_unicode

from app.schemas.metadata import Author
from app.services.importers.common import (
    ImportFormatError,
    ImportItem,
    ParseProblem,
    ParseResult,
    clean,
    extract_ids,
    make_item,
    parse_person,
    parse_year,
    split_keywords,
)

_ENTRY_START = re.compile(r"^[ \t]*@([A-Za-z]+)[ \t\r\n]*([{(])", re.MULTILINE)
_SCAN = re.compile(r"\\[\s\S]|[{}]|\n[ \t]*@[A-Za-z]+[ \t\r\n]*[{(]")
_KEY = re.compile(r"@[A-Za-z]+\s*[{(]\s*([^,\s{}]*)\s*,")
_VENUE_FIELDS = ("journal", "journaltitle", "booktitle", "container-title", "howpublished")
_FILE_MIME = re.compile(r"^(?P<rest>.*?):(?:application|text|image)/[\w.+-]+$")
_DRIVE = re.compile(r"^[A-Za-z]:[\\/]")
_AND = re.compile(r"\s+and\s+", re.IGNORECASE)


def looks_like_bibtex(text: str) -> bool:
    return _ENTRY_START.search(text[:200_000]) is not None


def split_entries(text: str) -> tuple[list[str], list[str]]:
    """``(entries, strings)``: raw text of each entry (``@comment`` / ``@preamble`` skipped) and ``@string`` definitions."""
    entries: list[str] = []
    strings: list[str] = []
    pos = 0
    while True:
        m = _ENTRY_START.search(text, pos)
        if m is None:
            break
        kind = m.group(1).lower()
        start = m.start()
        # Skip the whitespace the (multiline) anchor may have matched before "@".
        start += len(m.group(0)) - len(m.group(0).lstrip(" \t"))
        end = _entry_end(text, m.end() - 1, m.group(2))
        raw = text[start:end].strip()
        pos = max(end, m.end())
        if kind in ("comment", "preamble"):
            continue
        (strings if kind == "string" else entries).append(raw)
    return entries, strings


def _entry_end(text: str, open_at: int, opener: str) -> int:
    """Index just past the entry that opens at ``open_at``. A line that starts a new entry ends a broken one."""
    if opener == "(":  # rare: @article(key, ...): ends where the next entry starts
        nxt = _ENTRY_START.search(text, open_at + 1)
        return nxt.start() if nxt else len(text)
    depth = 0
    for m in _SCAN.finditer(text, open_at):
        tok = m.group(0)
        if tok[0] == "\\":
            continue
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
            if depth == 0:
                return m.end()
        elif m.start() > open_at:  # "\n@type{" inside an unclosed entry
            return m.start()
    return len(text)


def _tex(value: str | None) -> str:
    return clean(latex_to_unicode(value or "").replace("{", "").replace("}", ""))


def _split_authors(value: str) -> list[str]:
    """Split on ``and`` at brace depth 0 (so ``{Barnes and Noble}`` stays one name)."""
    parts: list[str] = []
    depth = 0
    tokens = re.split(r"(\{|\})", value)
    buf = ""
    for tok in tokens:
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth = max(0, depth - 1)
        if depth == 0 and tok not in ("{", "}"):
            pieces = _AND.split(tok)
            buf += pieces[0]
            for p in pieces[1:]:
                parts.append(buf)
                buf = p
        else:
            buf += tok
    parts.append(buf)
    return [p for p in (x.strip() for x in parts) if p]


def parse_authors(value: str | None) -> list[Author]:
    out: list[Author] = []
    for raw in _split_authors(value or ""):
        braced = raw.startswith("{") and raw.endswith("}") and raw.count("{") == 1
        name = parse_person(("{" + _tex(raw) + "}") if braced else _tex_name(raw))
        if name is not None:
            out.append(name)
    return out


def _tex_name(raw: str) -> str:
    # Decode accents but keep a comma-separated "Family, Given" intact.
    return clean(latex_to_unicode(raw).replace("{", "").replace("}", ""))


def parse_file_field(value: str | None) -> list[str]:
    """Paths from a ``file`` field: Zotero ``path:application/pdf``, JabRef ``Description:path:type``, plain paths,
    several joined with ``;``."""
    out: list[str] = []
    for part in re.split(r"(?<!\\);", value or ""):
        part = part.strip().replace("\\;", ";").replace("\\:", ":")
        if not part:
            continue
        m = _FILE_MIME.match(part)
        if m:
            part = m.group("rest")
        if not _DRIVE.match(part) and ":" in part and "://" not in part:
            part = part.split(":", 1)[1]  # "Full Text:files/1/a.pdf" -> path (description dropped)
        part = part.strip()
        if part:
            out.append(part)
    return out


def _item(entry: dict[str, str], raw: str, index: int) -> ImportItem | ParseProblem:
    key = entry.get("ID", "")
    ref = key or f"entry {index}"
    f = {k.lower(): v for k, v in entry.items()}
    venue = next((_tex(f[k]) for k in _VENUE_FIELDS if f.get(k, "").strip()), None)
    doi, arxiv = extract_ids(f.get("doi"), f.get("eprint"), f.get("url"), f.get("journal"), f.get("note"))
    year = parse_year(f.get("year")) or parse_year(f.get("date"))
    tags = split_keywords(_tex(f.get("keywords") or f.get("keyword")) + ";" + _tex(f.get("mendeley-tags")))
    notes = [_tex_block(f[k]) for k in ("note", "annote") if f.get(k, "").strip()]
    return make_item(
        ref=ref,
        title=_tex(f.get("title")),
        authors=parse_authors(f.get("author")) or parse_authors(f.get("editor")),
        year=year,
        venue=venue,
        doi=doi,
        arxiv_id=arxiv,
        abstract=_tex_block(f.get("abstract")),
        citekey=key,
        bibtex=raw.strip() + "\n",
        tags=tags,
        notes=notes,
        attachments=parse_file_field(f.get("file")),
        item_type=f.get("entrytype"),
    )


def _tex_block(value: str | None) -> str:
    return latex_to_unicode(value or "").replace("{", "").replace("}", "")


def parse(text: str) -> ParseResult:
    entries, strings = split_entries(text)
    if not entries:
        raise ImportFormatError("No BibTeX entries found")
    prefix = "\n".join(strings) + "\n" if strings else ""
    result = ParseResult(format="bibtex")
    for i, raw in enumerate(entries, 1):
        parser = BibTexParser(common_strings=True, ignore_nonstandard_types=False, homogenize_fields=False)
        parser.customization = None
        try:
            parsed = bibtexparser.loads(prefix + raw, parser).entries
        except Exception:  # noqa: BLE001 - pyparsing raises a zoo of exceptions on malformed input
            parsed = []
        km = _KEY.match(raw)
        label = (km.group(1) if km else "") or f"entry {i}"
        if not parsed:
            result.problems.append(ParseProblem(label, "could not parse the entry"))
            continue
        if _unbalanced(raw):
            result.problems.append(ParseProblem(label, "unbalanced braces (entry cut short)"))
            continue
        item = _item(parsed[0], raw, i)
        if isinstance(item, ParseProblem):
            result.problems.append(item)
        else:
            result.items.append(item)
    return result


def _unbalanced(raw: str) -> bool:
    depth = 0
    for m in re.finditer(r"\\[\s\S]|[{}]", raw):
        tok = m.group(0)
        if tok == "{":
            depth += 1
        elif tok == "}":
            depth -= 1
    return depth != 0


__all__ = ["parse", "looks_like_bibtex", "split_entries", "parse_authors", "parse_file_field"]
