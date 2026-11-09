"""The server-side import folder ``{IMPORT_DIR}/{workspace_slug}/``: listing and path confinement (spec R5, R8).

Every path that reaches the filesystem here (the bibliography file chosen from the listing, and each attachment
path written inside a bibliography file) goes through ``resolve_in_folder``. It works on the *text* of the path
first (no absolute path outside the folder, no ``..``, no NUL) and then walks the folder component by component
with ``lstat``: a symlink anywhere on the way, or a target that is not a regular file, is refused. Nothing is
resolved through ``Path.resolve()``, so a link cannot smuggle the path out of the folder.
"""

from __future__ import annotations

import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

BIBLIOGRAPHY_EXTENSIONS = {".bib": "bibtex", ".bibtex": "bibtex", ".ris": "ris", ".json": "csljson", ".rdf": "zotero_rdf", ".xml": "zotero_rdf"}
MAX_LISTED_FILES = 200
MAX_DEPTH = 3
MAX_SOURCE_LEN = 300
_DRIVE = re.compile(r"^[A-Za-z]:")


class UnsafePath(ValueError):
    """The path points outside the import folder, through a symlink, or at something that is not a file."""


@dataclass(frozen=True)
class Candidate:
    name: str  # path relative to the folder, "/" separated
    size: int
    format: str  # guessed from the extension; the content decides at import time


def workspace_folder(import_dir: Path, slug: str) -> Path:
    return Path(import_dir) / slug


def _split(raw: str) -> tuple[bool, list[str]]:
    """``(absolute, components)`` of a path as written in a file, or ``UnsafePath``."""
    text = (raw or "").strip()
    if not text or "\x00" in text:
        raise UnsafePath("empty or invalid path")
    if text[:5].lower() == "file:":  # file:///abs/x.pdf, file://files/1/x.pdf (relative), file:x.pdf
        text = text[5:]
        if text.startswith("//"):
            text = text[2:]
            if text[:9].lower() == "localhost":
                text = text[9:]
        text = unquote(text)
        if "\x00" in text:
            raise UnsafePath("empty or invalid path")
    text = text.replace("\\", "/")
    if _DRIVE.match(text):
        raise UnsafePath("absolute path outside the import folder")
    absolute = text.startswith("/")
    parts = [p for p in text.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise UnsafePath("path contains '..'")
    return absolute, parts


def resolve_in_folder(folder: Path, raw: str, *, base: str = "") -> tuple[Path, str]:
    """The regular file ``raw`` names, confined to ``folder``. Returns ``(absolute path, relative posix path)``.

    A relative ``raw`` is taken relative to ``base`` (the folder of the bibliography file, itself relative to
    ``folder``). An absolute ``raw`` must start with the folder's own absolute path. Raises ``UnsafePath``.
    """
    root = Path(os.path.abspath(folder))
    absolute, parts = _split(raw)
    if absolute:
        root_parts = list(root.parts[1:])
        if parts[: len(root_parts)] != root_parts:
            raise UnsafePath("absolute path outside the import folder")
        rel = parts[len(root_parts) :]
    else:
        rel = [p for p in base.split("/") if p] + parts
    if not rel:
        raise UnsafePath("not a file")
    current = root
    try:
        for i, name in enumerate(rel):
            current = current / name
            st = os.lstat(current)
            if stat.S_ISLNK(st.st_mode):
                raise UnsafePath("symbolic links are not followed")
            last = i == len(rel) - 1
            if last and not stat.S_ISREG(st.st_mode):
                raise UnsafePath("not a regular file")
            if not last and not stat.S_ISDIR(st.st_mode):
                raise UnsafePath("not a file")
    except FileNotFoundError:
        raise UnsafePath("file not found in the import folder") from None
    except OSError:
        raise UnsafePath("file cannot be read") from None
    return current, "/".join(rel)


def validate_source_name(name: str) -> str:
    """The bibliography file name sent by the client: a relative path without surprises and a known extension."""
    if not name or len(name) > MAX_SOURCE_LEN or "\\" in name or name.startswith("/"):
        raise UnsafePath("invalid file name")
    if Path(name).suffix.lower() not in BIBLIOGRAPHY_EXTENSIONS:
        raise UnsafePath("not a bibliography file")
    return name


def list_candidates(folder: Path) -> list[Candidate]:
    """Bibliography files (by extension) in the folder and up to ``MAX_DEPTH`` levels below it.

    Hidden entries and symlinks (files or directories) are skipped, so everything listed passes
    ``resolve_in_folder``. Returns an empty list when the folder does not exist.
    """
    out: list[Candidate] = []

    def walk(directory: Path, rel: list[str], depth: int) -> None:
        try:
            entries = sorted(os.scandir(directory), key=lambda e: e.name)
        except OSError:
            return
        for entry in entries:
            if len(out) >= MAX_LISTED_FILES:
                return
            if entry.name.startswith(".") or entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                if depth < MAX_DEPTH:
                    walk(Path(entry.path), rel + [entry.name], depth + 1)
                continue
            fmt = BIBLIOGRAPHY_EXTENSIONS.get(PurePosixPath(entry.name).suffix.lower())
            if fmt is None or not entry.is_file(follow_symlinks=False):
                continue
            name = "/".join(rel + [entry.name])
            if len(name) <= MAX_SOURCE_LEN:
                out.append(Candidate(name=name, size=entry.stat(follow_symlinks=False).st_size, format=fmt))

    walk(Path(folder), [], 0)
    return out
