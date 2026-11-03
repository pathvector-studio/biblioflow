"""Staging (.incoming) and shared placement of uploaded files inside the library root."""

from __future__ import annotations

import hashlib
import logging
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

from app.services.pdf import PDF_MAGIC_WINDOW
from app.services.storage.paths import PathTraversalError, resolve_in_library, with_collision_index

logger = logging.getLogger("biblioflow.storage")

INCOMING_NAME = ".incoming"
MAX_COLLISION_INDEX = 50
HASH_BLOCK = 1024 * 1024


class TooLarge(Exception):
    """The stream exceeded the configured size limit."""


class PlacementError(Exception):
    """No free target path could be found."""


@dataclass(frozen=True)
class StagedFile:
    path: Path
    sha256: str
    size: int
    head: bytes


@dataclass(frozen=True)
class PlacedFile:
    relative: str
    created: bool  # False when an identical file was already present and reused


def incoming_dir(root: str | Path) -> Path:
    d = Path(root) / INCOMING_NAME
    d.mkdir(mode=0o750, parents=True, exist_ok=True)
    return d


async def receive_stream(chunks: AsyncIterator[bytes], root: str | Path, max_bytes: int) -> StagedFile:
    """Write ``chunks`` to ``.incoming/{uuid}.part`` while hashing; abort when over ``max_bytes``."""
    path = incoming_dir(root) / f"{uuid.uuid4().hex}.part"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o640)
    sha = hashlib.sha256()
    size = 0
    head = b""
    ok = False
    try:
        with os.fdopen(fd, "wb") as fh:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > max_bytes:
                    raise TooLarge(f"stream exceeds {max_bytes} bytes")
                if len(head) < PDF_MAGIC_WINDOW:
                    head = (head + chunk)[:PDF_MAGIC_WINDOW]
                sha.update(chunk)
                fh.write(chunk)
        ok = True
    finally:
        if not ok:
            path.unlink(missing_ok=True)
    return StagedFile(path=path, sha256=sha.hexdigest(), size=size, head=head)


def discard(staged: StagedFile) -> None:
    staged.path.unlink(missing_ok=True)


def sha256_file(path: Path) -> str:
    sha = hashlib.sha256()
    with open(path, "rb") as fh:
        while block := fh.read(HASH_BLOCK):
            sha.update(block)
    return sha.hexdigest()


def place_file(staged: StagedFile, relative: str, root: str | Path) -> PlacedFile:
    """Hard-link the staged file to its library path, sharing identical content. Always removes the temp file."""
    try:
        candidates = [relative] + [with_collision_index(relative, n) for n in range(2, MAX_COLLISION_INDEX + 1)]
        for cand in candidates:
            target = resolve_in_library(cand, root)
            target.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(staged.path, target, follow_symlinks=False)
                return PlacedFile(relative=cand, created=True)
            except FileExistsError:
                pass
            except NotImplementedError:  # pragma: no cover - platform without follow_symlinks support
                os.link(staged.path, target)
                return PlacedFile(relative=cand, created=True)
            # Target exists: never trust symlinks; reuse only identical regular files.
            if target.is_symlink() or not target.is_file():
                continue
            if target.stat().st_size == staged.size and sha256_file(target) == staged.sha256:
                return PlacedFile(relative=cand, created=False)
        raise PlacementError("no free path for file")
    except PathTraversalError as exc:
        raise PlacementError(str(exc)) from exc
    finally:
        staged.path.unlink(missing_ok=True)
