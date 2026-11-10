"""CSL JSON reader (Zotero, Mendeley, pandoc, BiblioFlow's own export)."""

from __future__ import annotations

import json
from typing import Any

from app.schemas.metadata import Author
from app.services.importers.common import (
    ImportFormatError,
    ParseProblem,
    ParseResult,
    clean,
    extract_ids,
    make_item,
    parse_person,
    parse_year,
    split_keywords,
)


def _text(v: Any) -> str:
    for _ in range(8):  # ["title"] and the like; deeper nesting is not CSL
        if not isinstance(v, list):
            break
        v = v[0] if v else ""
    if isinstance(v, str):
        return v
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return ""


def _authors(raw: Any) -> list[Author]:
    out: list[Author] = []
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        family, given, literal = clean(_text(a.get("family"))), clean(_text(a.get("given"))), clean(_text(a.get("literal")))
        if family:
            out.append(Author(family=family[:200], given=given[:200] or None))
        elif literal:
            person = parse_person(literal)
            if person:
                out.append(person)
    return out


def _year(item: dict[str, Any]) -> int | None:
    issued = item.get("issued")
    if isinstance(issued, dict):
        parts = issued.get("date-parts")
        if isinstance(parts, list) and parts and isinstance(parts[0], list) and parts[0]:
            y = parts[0][0]
            if isinstance(y, (int, str)) and not isinstance(y, bool):
                return parse_year(y)
        return parse_year(issued.get("raw") or issued.get("literal"))
    return parse_year(issued)


def parse(text: str) -> ParseResult:
    try:
        data = json.loads(text.lstrip("﻿"))
    except ValueError as exc:
        raise ImportFormatError(f"Invalid JSON: {exc}") from None
    except RecursionError:
        raise ImportFormatError("Invalid JSON: nested too deeply") from None
    if isinstance(data, dict):
        data = data.get("items") if isinstance(data.get("items"), list) else [data]
    if not isinstance(data, list) or not data:
        raise ImportFormatError("No CSL JSON items found")
    result = ParseResult(format="csljson")
    for i, raw in enumerate(data, 1):
        if not isinstance(raw, dict):
            result.problems.append(ParseProblem(f"item {i}", "not a JSON object"))
            continue
        doi, arxiv = extract_ids(_text(raw.get("DOI")), _text(raw.get("URL")), _text(raw.get("note")))
        item = make_item(
            ref=clean(_text(raw.get("title")))[:60] or f"item {i}",
            title=_text(raw.get("title")),
            authors=_authors(raw.get("author")) or _authors(raw.get("editor")),
            year=_year(raw),
            venue=_text(raw.get("container-title")) or _text(raw.get("event-title")) or None,
            doi=doi,
            arxiv_id=arxiv,
            abstract=_text(raw.get("abstract")),
            citekey=_text(raw.get("citation-key")) or _text(raw.get("id")),
            tags=split_keywords(_text(raw.get("keyword"))),
            item_type=_text(raw.get("type")),
        )
        if isinstance(item, ParseProblem):
            result.problems.append(item)
        else:
            result.items.append(item)
    return result
