"""RIS reader (EndNote, Zotero, Mendeley Reference Manager and Mendeley Desktop exports)."""

from __future__ import annotations

import re

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

_LINE = re.compile(r"^([A-Z][A-Z0-9])[ \t]{1,2}-(?:[ \t]?(.*))?$")
_START = re.compile(r"^TY[ \t]{1,2}-", re.MULTILINE)
_TITLE_TAGS = ("TI", "T1", "CT", "BT")
_VENUE_TAGS = ("JO", "JF", "T2", "JA", "J2", "BT")
_ABSTRACT_TAGS = ("AB", "N2")
_FILE_TAGS = ("L1", "L2", "L4")


def looks_like_ris(text: str) -> bool:
    return _START.search(text[:200_000]) is not None


def _records(text: str) -> list[list[tuple[str, str]]]:
    records: list[list[tuple[str, str]]] = []
    cur: list[tuple[str, str]] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        m = _LINE.match(line)
        if m:
            tag, value = m.group(1), (m.group(2) or "").strip()
            if tag == "TY" and cur:  # a record that lacks its ER line
                records.append(cur)
                cur = []
            if tag == "ER":
                if cur:
                    records.append(cur)
                cur = []
            else:
                cur.append((tag, value))
        elif cur and line.strip():
            # Continuation of the previous value (long abstracts / notes wrapped by some tools).
            tag, value = cur[-1]
            cur[-1] = (tag, f"{value}\n{line.strip()}" if tag in ("N1", "AB", "N2") else f"{value} {line.strip()}")
    if cur:
        records.append(cur)
    return records


def parse(text: str) -> ParseResult:
    text = text.lstrip("﻿")
    records = _records(text)
    if not records:
        raise ImportFormatError("No RIS records found")
    result = ParseResult(format="ris")
    for i, rec in enumerate(records, 1):
        fields: dict[str, list[str]] = {}
        for tag, value in rec:
            if value:
                fields.setdefault(tag, []).append(value)

        def first(*tags: str) -> str | None:
            for t in tags:
                if fields.get(t):
                    return fields[t][0]
            return None

        ref_id = first("ID")
        title = first(*_TITLE_TAGS)
        ref = clean(title)[:60] or f"record {i}"
        authors = [a for a in (parse_person(v) for t in ("AU", "A1") for v in fields.get(t, [])) if a]
        year = parse_year(first("PY")) or parse_year(first("Y1")) or parse_year(first("DA"))
        doi, arxiv = extract_ids(first("DO"), *fields.get("UR", []), first("JO", "JF", "T2"), first("M3"))
        tags = [t for v in fields.get("KW", []) for t in split_keywords(v)]
        item = make_item(
            ref=ref,
            title=title,
            authors=authors,
            year=year,
            venue=first(*_VENUE_TAGS) if first(*_VENUE_TAGS) != title else None,
            doi=doi,
            arxiv_id=arxiv,
            abstract=first(*_ABSTRACT_TAGS),
            citekey=ref_id,
            tags=tags,
            notes=fields.get("N1", []),
            attachments=[v for t in _FILE_TAGS for v in fields.get(t, [])] + fields.get("UR", []),
            item_type=first("TY"),
        )
        if isinstance(item, ParseProblem):
            result.problems.append(item)
        else:
            result.items.append(item)
    return result
