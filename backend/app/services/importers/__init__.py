"""Readers for bibliography files: BibTeX, RIS, CSL JSON and Zotero RDF.

``parse_file`` returns the records and the entries that could not be read. Nothing here touches the network
or the database, and no path or URL found in a file is opened.
"""

from __future__ import annotations

from app.services.importers import bibtex, csljson, ris, zotero_rdf
from app.services.importers.common import ImportFormatError, ImportItem, ParseProblem, ParseResult

FORMATS = ("bibtex", "ris", "csljson", "zotero_rdf")

__all__ = ["FORMATS", "ImportFormatError", "ImportItem", "ParseProblem", "ParseResult", "detect_format", "parse_file"]


def decode(data: bytes) -> str:
    """UTF-8 (with or without BOM), falling back to UTF-16 with BOM, then Latin-1 (old RIS / BibTeX exports)."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("latin-1")


def detect_format(text: str, filename: str | None = None) -> str:
    """Sniff the content first, then fall back to the file extension."""
    head = text[:4000].lstrip("﻿ \t\r\n")
    if head.startswith("<") and zotero_rdf.looks_like_rdf(text):
        return "zotero_rdf"
    if head.startswith(("[", "{")):
        return "csljson"
    if ris.looks_like_ris(text):
        return "ris"
    if bibtex.looks_like_bibtex(text):
        return "bibtex"
    name = filename or ""
    ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""
    by_ext = {"bib": "bibtex", "bibtex": "bibtex", "ris": "ris", "json": "csljson", "rdf": "zotero_rdf", "xml": "zotero_rdf"}
    if ext in by_ext:
        return by_ext[ext]
    raise ImportFormatError("Unrecognized file format (expected BibTeX, RIS, CSL JSON or Zotero RDF)")


def parse_file(data: bytes, filename: str | None = None) -> ParseResult:
    text = decode(data)
    fmt = detect_format(text, filename)
    if fmt == "bibtex":
        return bibtex.parse(text)
    if fmt == "ris":
        return ris.parse(text)
    if fmt == "csljson":
        return csljson.parse(text)
    return zotero_rdf.parse(text)
