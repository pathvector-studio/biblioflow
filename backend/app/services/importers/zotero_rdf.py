"""Zotero RDF reader (Zotero's "Export Library ... Zotero RDF", with or without "Export Files").

Items, ``dc:subject`` tags, nested ``z:Collection`` (``dcterms:hasPart``), ``bib:Memo`` notes and attachment paths
(``z:Attachment`` / ``rdf:resource``). The XML is read with ``defusedxml`` (no DTDs, entities or external
references); no URL in the file is ever fetched.
"""

from __future__ import annotations

import re
from html.parser import HTMLParser
from xml.etree.ElementTree import Element

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import ParseError, fromstring

from app.schemas.metadata import Author
from app.services.importers.common import (
    MAX_COLLECTION_DEPTH,
    ImportFormatError,
    ParseProblem,
    ParseResult,
    clean,
    extract_ids,
    make_item,
    parse_person,
    parse_year,
)

NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "z": "http://www.zotero.org/namespaces/export#",
    "dc": "http://purl.org/dc/elements/1.1/",
    "dcterms": "http://purl.org/dc/terms/",
    "bib": "http://purl.org/net/biblio#",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "link": "http://purl.org/rss/1.0/modules/link/",
    "prism": "http://prismstandard.org/namespaces/1.2/basic/",
}


def _q(name: str) -> str:
    prefix, local = name.split(":")
    return f"{{{NS[prefix]}}}{local}"


RDF_ABOUT, RDF_RESOURCE, RDF_VALUE = _q("rdf:about"), _q("rdf:resource"), _q("rdf:value")
_SKIP_TOP = {_q("z:Collection"), _q("z:Attachment"), _q("bib:Memo")}
_PEOPLE = ("bib:authors", "bib:editors", "z:translators", "bib:contributors")
_MAX_ITEM_NOTES = 50


def looks_like_rdf(text: str) -> bool:
    head = text[:4000].lstrip("﻿ \t\r\n")
    return head.startswith("<") and "rdf" in head.lower()


class _TextExtractor(HTMLParser):
    """Zotero notes are HTML: keep paragraphs, line breaks and list items as plain text."""

    _BLOCK = {"p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "blockquote", "pre"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "li":
            self.parts.append("\n- ")
        elif tag in self._BLOCK:
            self.parts.append("\n\n" if tag != "br" else "\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK and tag not in ("br", "li"):
            self.parts.append("\n\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def html_to_text(html: str) -> str:
    if "<" not in html:
        return html.strip()
    p = _TextExtractor()
    try:
        p.feed(html)
        p.close()
    except Exception:  # noqa: BLE001 - malformed HTML: fall back to a crude tag strip
        return re.sub(r"<[^>]+>", " ", html).strip()
    text = "".join(p.parts)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+\n", "\n", text)).strip()


def _text(el: Element | None, path: str) -> str:
    if el is None:
        return ""
    found = el.find(path, NS)
    if found is None:
        return ""
    return clean("".join(found.itertext()))


def _ref_id(value: str | None) -> str:
    return (value or "").lstrip("#")


def _people(el: Element) -> list[Author]:
    out: list[Author] = []
    for tag in _PEOPLE:
        for person in el.findall(f"{tag}//foaf:Person", NS):
            surname, given = _text(person, "foaf:surname"), _text(person, "foaf:givenName")
            if surname:
                out.append(Author(family=surname[:200], given=given[:200] or None))
            elif _text(person, "foaf:name"):
                a = parse_person(_text(person, "foaf:name"))
                if a:
                    out.append(a)
        if out:
            break  # authors win over editors
    return out


def _identifier_texts(el: Element) -> list[str]:
    out = []
    for ident in el.findall("dc:identifier", NS):
        out.append(clean("".join(ident.itertext())))
    for value in el.findall("dc:identifier//rdf:value", NS):
        out.append(clean(value.text))
    for rel in el.findall("dc:relation", NS):
        out.append(clean("".join(rel.itertext())))
    return [t for t in out if t]


def _venue(el: Element) -> str | None:
    for part in el.findall("dcterms:isPartOf", NS):
        for title in part.iter(_q("dc:title")):
            t = clean(title.text)
            if t:
                return t
    return _text(el, "z:proceedingsTitle") or _text(el, "prism:publicationName") or None


def _tags(el: Element) -> list[str]:
    out = []
    for subj in el.findall("dc:subject", NS):
        value = subj.find(".//rdf:value", NS)
        out.append(clean(value.text if value is not None else "".join(subj.itertext())))
    return [t for t in out if t]


def _collection_tree(root: Element) -> tuple[dict[str, str], dict[str, list[str]], dict[str, list[str]]]:
    """``(names, children, members)`` keyed by the collection's ``rdf:about`` id."""
    names: dict[str, str] = {}
    children: dict[str, list[str]] = {}
    members: dict[str, list[str]] = {}
    for col in root.findall("z:Collection", NS):
        cid = _ref_id(col.get(RDF_ABOUT))
        if not cid:
            continue
        names[cid] = _text(col, "dc:title") or "Untitled collection"
        children[cid], members[cid] = [], []
        for part in col.findall("dcterms:hasPart", NS):
            members[cid].append(_ref_id(part.get(RDF_RESOURCE)))
    for cid in names:
        children[cid] = [m for m in members[cid] if m in names]
    return names, children, members


def _collection_paths(root: Element) -> dict[str, list[list[str]]]:
    """item id -> collection paths (root-first names). Cycles and absurd depth are cut off."""
    names, children, members = _collection_tree(root)
    nested = {c for kids in children.values() for c in kids}
    out: dict[str, list[list[str]]] = {}

    def walk(cid: str, path: list[str], seen: frozenset[str]) -> None:
        path = [*path, names[cid]]
        for m in members[cid]:
            if m not in names:
                out.setdefault(m, []).append(path)
        if len(path) >= MAX_COLLECTION_DEPTH:
            return
        for child in children[cid]:
            if child not in seen:
                walk(child, path, seen | {child})

    for cid in names:
        if cid not in nested:
            walk(cid, [], frozenset({cid}))
    return out


def parse(text: str) -> ParseResult:
    try:
        root = fromstring(text.lstrip("﻿").encode("utf-8"), forbid_dtd=True)
    except (ParseError, DefusedXmlException, ValueError) as exc:
        raise ImportFormatError(f"Invalid or unsafe XML: {type(exc).__name__}") from None
    if root.tag != _q("rdf:RDF"):
        raise ImportFormatError("Not a Zotero RDF file (no rdf:RDF root)")

    by_id: dict[str, Element] = {}
    for el in root:
        rid = _ref_id(el.get(RDF_ABOUT))
        if rid:
            by_id[rid] = el
    collections = _collection_paths(root)

    result = ParseResult(format="zotero_rdf")
    index = 0
    for el in root:
        if el.tag in _SKIP_TOP or not isinstance(el.tag, str):
            continue
        item_type = _text(el, "z:itemType")
        if item_type in ("attachment", "note"):
            continue
        title_raw = _text(el, "dc:title")
        if not title_raw and not item_type:
            continue  # some other top-level node (a Series, a Journal, ...)
        index += 1
        rid = _ref_id(el.get(RDF_ABOUT))
        notes: list[str] = []
        attachments: list[str] = []
        for tag in ("link:link", "dcterms:isReferencedBy"):
            for ref in el.findall(tag, NS):
                child = by_id.get(_ref_id(ref.get(RDF_RESOURCE)))
                if child is None:
                    continue
                if child.tag == _q("bib:Memo"):
                    value = child.find("rdf:value", NS)
                    note = html_to_text("".join(value.itertext()) if value is not None else "")
                    if note:
                        notes.append(note)
                elif child.tag == _q("z:Attachment"):
                    attachments.extend(_attachment_paths(child))
        doi, arxiv = extract_ids(*_identifier_texts(el))
        item = make_item(
            ref=title_raw[:60] or f"item {index}",
            title=title_raw,
            authors=_people(el),
            year=parse_year(_text(el, "dc:date")),
            venue=_venue(el),
            doi=doi,
            arxiv_id=arxiv,
            abstract=_text(el, "dcterms:abstract"),
            tags=_tags(el),
            collections=collections.get(rid, []),
            notes=notes[:_MAX_ITEM_NOTES],
            attachments=attachments,
            item_type=item_type,
        )
        if isinstance(item, ParseProblem):
            result.problems.append(item)
        else:
            result.items.append(item)
    if not result.items and not result.problems:
        raise ImportFormatError("No items found in the Zotero RDF file")
    return result


def _attachment_paths(att: Element) -> list[str]:
    """The stored / linked file path of an attachment (``files/12/paper.pdf``); web links are not paths."""
    link_mode = _text(att, "z:linkMode")
    if link_mode == "3":  # linked URL: nothing on disk
        return []
    out = []
    for res in att.findall("rdf:resource", NS):
        value = (res.get(RDF_RESOURCE) or "").strip()
        if value:
            out.append(value)
    return out
