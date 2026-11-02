"""Filename sanitizers and library path resolution (path traversal defence)."""

from __future__ import annotations

import base64
import re
import string
import unicodedata
import uuid
from pathlib import Path, PurePosixPath

DEFAULT_TEMPLATE = "{year}/{identifier}_{slug_title}.pdf"
ALLOWED_PLACEHOLDERS = frozenset({"year", "identifier", "slug_title"})
MAX_IDENTIFIER_LEN = 100
MAX_PART_LEN = 200
MAX_RELATIVE_LEN = 1024
UNKNOWN_YEAR = "Unknown"

_LITERAL_ALLOWED = re.compile(r"^[A-Za-z0-9._\-/]*$")


class PathTraversalError(ValueError):
    """Raised when a path would escape the library root."""


def sanitize_identifier(raw: str) -> str:
    """Return a filesystem-safe identifier (e.g. ``10.1145/3651890`` -> ``10.1145_3651890``)."""
    if not isinstance(raw, str):
        raise ValueError("identifier must be a string")
    s = unicodedata.normalize("NFKC", raw)
    # Drop NUL, control and format characters (category C*).
    s = "".join(ch for ch in s if not unicodedata.category(ch).startswith("C"))
    s = re.sub(r"[/\\:\s]", "_", s)
    s = re.sub(r"[^A-Za-z0-9._-]", "", s)
    s = re.sub(r"\.{2,}", ".", s)
    s = s.lstrip("._-")
    s = s[:MAX_IDENTIFIER_LEN].rstrip(".")
    if s in ("", ".", ".."):
        raise ValueError("identifier is empty after sanitization")
    return s


def slugify_title(title: str | None, max_len: int = 50) -> str:
    """Lowercase ASCII slug of ``title`` limited to ``max_len`` chars on a word boundary."""
    s = unicodedata.normalize("NFKD", title or "")
    s = s.encode("ascii", "ignore").decode("ascii").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    if len(s) > max_len:
        head = s[:max_len]
        if s[max_len] != "-" and "-" in head:
            head = head.rsplit("-", 1)[0]
        s = head.strip("-")
    return s or "untitled"


def generate_internal_identifier() -> str:
    """``BF-`` + 10 chars of base32 derived from a random UUID."""
    return "BF-" + base64.b32encode(uuid.uuid4().bytes).decode("ascii")[:10]


def _check_parts(relative: str) -> None:
    if not relative or len(relative) > MAX_RELATIVE_LEN:
        raise ValueError("invalid relative path length")
    if "\x00" in relative or "\\" in relative or relative.startswith("/") or "//" in relative or relative.endswith("/"):
        raise ValueError("invalid characters in relative path")
    for part in PurePosixPath(relative).parts:
        if part in ("", ".", "..") or "/" in part or part.startswith("."):
            raise ValueError(f"invalid path component: {part!r}")
        if len(part) > MAX_PART_LEN:
            raise ValueError("path component too long")


def validate_template(tpl: str) -> str:
    """Validate a ``FILENAME_TEMPLATE``; return it unchanged or raise ``ValueError``."""
    if not isinstance(tpl, str) or not tpl or len(tpl) > 200:
        raise ValueError("template must be a non-empty string (<=200 chars)")
    if not tpl.endswith(".pdf"):
        raise ValueError("template must end with '.pdf'")
    seen: set[str] = set()
    for literal, field, spec, conv in string.Formatter().parse(tpl):
        if "{" in literal or "}" in literal:
            raise ValueError("braces are not allowed in literal text")
        if not _LITERAL_ALLOWED.match(literal):
            raise ValueError("template literal contains disallowed characters")
        if ".." in literal:
            raise ValueError("'..' is not allowed in template")
        if field is None:
            continue
        if field not in ALLOWED_PLACEHOLDERS:
            raise ValueError(f"unsupported placeholder: {{{field}}}")
        if spec or conv:
            raise ValueError("format specs and conversions are not allowed")
        seen.add(field)
    if "identifier" not in seen:
        raise ValueError("template must contain {identifier}")
    sample = tpl.format(year="2024", identifier="X", slug_title="x")
    _check_parts(sample)
    return tpl


def _year_str(year: int | str | None) -> str:
    if isinstance(year, bool):
        return UNKNOWN_YEAR
    if isinstance(year, str) and year.isascii() and year.isdigit():
        year = int(year)
    if isinstance(year, int) and 1 <= year <= 9999:
        return str(year)
    return UNKNOWN_YEAR


def build_relative_path(
    year: int | str | None,
    identifier: str,
    title: str | None,
    template: str = DEFAULT_TEMPLATE,
) -> str:
    """Render the storage-relative path (POSIX) for a paper."""
    validate_template(template)
    rel = template.format(
        year=_year_str(year),
        identifier=sanitize_identifier(identifier),
        slug_title=slugify_title(title),
    )
    _check_parts(rel)
    return rel


def resolve_in_library(relative: str, root: str | Path) -> Path:
    """Resolve ``relative`` under ``root``; raise ``PathTraversalError`` if it escapes."""
    if not isinstance(relative, str) or not relative or "\x00" in relative:
        raise PathTraversalError("invalid relative path")
    if relative.startswith(("/", "\\")) or Path(relative).is_absolute():
        raise PathTraversalError("absolute paths are not allowed")
    root_resolved = Path(root).resolve()
    target = (root_resolved / relative).resolve()
    if target == root_resolved or not target.is_relative_to(root_resolved):
        raise PathTraversalError("path escapes library root")
    return target
