"""PDF validation and light metadata extraction (pypdf).

Untrusted PDFs are parsed in a child process (``inspect_pdf_isolated``) with CPU, memory and wall-clock
limits, so a malicious file cannot hang or exhaust the API / worker process.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pypdf import PdfReader

from app.services.metadata.identifiers import (
    compact_letter_spacing,
    find_identifiers_in_text,
    find_pii,
    strip_download_suffix,
)

if TYPE_CHECKING:
    from app.services.chunking import ExtractedChunks

logger = logging.getLogger("biblioflow.pdf")

PDF_MAGIC_WINDOW = 1024
MAX_TEXT_CHARS = 20_000
MAX_TEXT_PAGES = 2
MAX_PAGES = 20_000
MAX_TITLE_LEN = 500

_JUNK_TITLE = re.compile(
    r"^(untitled.*|microsoft word\s*-.*|.*\.(docx?|tex|pdf|dvi|indd|pptx?)|slide \d+|document\d*|none|null|\s*)$",
    re.IGNORECASE,
)


class InvalidPdf(ValueError):
    """The file is not a readable PDF (corrupt, password-protected, ...)."""


@dataclass(frozen=True)
class PdfInfo:
    page_count: int
    title: str | None
    doi: str | None
    arxiv_id: str | None
    first_page_text: str = ""
    pii: str | None = None


def has_pdf_magic(head: bytes) -> bool:
    return b"%PDF-" in head[:PDF_MAGIC_WINDOW]


def _clean_title(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    title = strip_download_suffix(" ".join(raw.replace("\x00", " ").split()))[:MAX_TITLE_LEN]
    if len(title) < 3 or _JUNK_TITLE.match(title):
        return None
    return title


def inspect_pdf(path: str | Path) -> PdfInfo:
    """Open ``path`` with pypdf; raise ``InvalidPdf`` if unreadable or encrypted."""
    try:
        with open(path, "rb") as fh:
            reader = PdfReader(fh, strict=False)
            if reader.is_encrypted:
                try:
                    ok = reader.decrypt("")  # empty user password only
                except Exception as exc:  # noqa: BLE001
                    raise InvalidPdf("encrypted PDF") from exc
                if not ok:
                    raise InvalidPdf("password-protected PDF")
            page_count = len(reader.pages)
            if page_count < 1:
                raise InvalidPdf("PDF has no pages")
            if page_count > MAX_PAGES:
                raise InvalidPdf("PDF has too many pages")

            title = None
            raw_title = ""
            try:
                meta = reader.metadata
                raw_title = meta.title if meta is not None and isinstance(meta.title, str) else ""
                title = _clean_title(raw_title)
            except Exception:  # noqa: BLE001
                logger.debug("could not read PDF metadata", exc_info=True)

            chunks: list[str] = []
            first_page_text = ""
            total = 0
            for i in range(min(MAX_TEXT_PAGES, page_count)):
                try:
                    text = reader.pages[i].extract_text() or ""
                except Exception:  # noqa: BLE001
                    logger.debug("text extraction failed on page %d", i, exc_info=True)
                    continue
                if i == 0:
                    first_page_text = text[:MAX_TEXT_CHARS]
                chunks.append(text[: MAX_TEXT_CHARS - total])
                total += len(chunks[-1])
                if total >= MAX_TEXT_CHARS:
                    break
            text = "\n".join(chunks)
            doi, arxiv_id = find_identifiers_in_text(text)
            compact = compact_letter_spacing(text)
            if doi is None and arxiv_id is None and compact != text:
                doi, arxiv_id = find_identifiers_in_text(compact)
            pii = find_pii(raw_title) or find_pii(text) or find_pii(compact)
            return PdfInfo(
                page_count=page_count, title=title, doi=doi, arxiv_id=arxiv_id, first_page_text=first_page_text, pii=pii
            )
    except InvalidPdf:
        raise
    except RecursionError as exc:
        raise InvalidPdf("PDF structure too deep") from exc
    except Exception as exc:  # noqa: BLE001 - pypdf raises a wide variety of errors on bad input
        raise InvalidPdf("unreadable PDF") from exc


# ------------------------------------------------------------------ isolation
PARSE_TIMEOUT_SECONDS = 30
PARSE_CPU_SECONDS = 30
PARSE_MEMORY_BYTES = 1024 * 1024 * 1024
_MAX_RESULT_BYTES = 256 * 1024
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


CHUNK_TIMEOUT_SECONDS = 120
CHUNK_CPU_SECONDS = 120
_MAX_CHUNK_RESULT_BYTES = 32 * 1024 * 1024


def _limit_self(cpu_seconds: int = PARSE_CPU_SECONDS) -> None:
    """Applied by the child itself (``preexec_fn`` is unsafe in the threaded parent)."""
    import resource

    resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
    resource.setrlimit(resource.RLIMIT_AS, (PARSE_MEMORY_BYTES, PARSE_MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))


def _probe_command(path: Path) -> list[str]:
    return [sys.executable, "-m", "app.services.pdf", str(path)]


def _chunks_command(path: Path) -> list[str]:
    return [sys.executable, "-m", "app.services.pdf", "--chunks", str(path)]


async def _run_child(cmd: list[str], timeout: float, max_bytes: int) -> dict[str, Any]:
    """Run ``cmd`` with a minimal environment (no secrets), kill it on timeout, return its JSON output."""
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONPATH": str(_PROJECT_ROOT), "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"}
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        cwd=str(_PROJECT_ROOT),
        env=env,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        raise InvalidPdf("PDF took too long to parse") from None
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    if proc.returncode != 0 or len(out) > max_bytes:
        raise InvalidPdf("unreadable PDF")
    try:
        data = json.loads(out)
    except ValueError:
        raise InvalidPdf("unreadable PDF") from None
    if "error" in data:
        raise InvalidPdf(str(data["error"])[:200])
    return data


async def inspect_pdf_isolated(path: str | Path, timeout: float = PARSE_TIMEOUT_SECONDS) -> PdfInfo:
    """``inspect_pdf`` in a resource-limited child process. Raises ``InvalidPdf`` on any failure or timeout.

    The child gets a minimal environment (no secrets) and is killed when ``timeout`` expires.
    """
    data = await _run_child(_probe_command(Path(path)), timeout, _MAX_RESULT_BYTES)
    return PdfInfo(**data["info"])


async def extract_chunks_isolated(path: str | Path, timeout: float = CHUNK_TIMEOUT_SECONDS) -> "ExtractedChunks":
    """Page text with coordinates, split into chunks, extracted in the same kind of child process."""
    from app.services.chunking import Chunk, ExtractedChunks  # noqa: PLC0415

    data = (await _run_child(_chunks_command(Path(path)), timeout, _MAX_CHUNK_RESULT_BYTES))["chunks"]
    return ExtractedChunks(page_count=data["page_count"], chunks=[Chunk(**c) for c in data["chunks"]])


def _main(argv: list[str]) -> int:
    """Child entry point: print ``{"info": {...}}`` or ``{"error": "..."}`` as JSON on stdout.

    ``--chunks PATH`` prints ``{"chunks": {"page_count": n, "chunks": [...]}}`` instead.
    """
    chunks = argv[:1] == ["--chunks"]
    _limit_self(CHUNK_CPU_SECONDS if chunks else PARSE_CPU_SECONDS)
    try:
        if chunks:
            from app.services.chunking import extract_chunks  # noqa: PLC0415

            result: dict[str, object] = {"chunks": asdict(extract_chunks(argv[1]))}
        else:
            result = {"info": asdict(inspect_pdf(argv[0]))}
    except InvalidPdf as exc:
        result = {"error": str(exc)}
    except MemoryError:
        result = {"error": "PDF needs too much memory"}
    sys.stdout.write(json.dumps(result))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through inspect_pdf_isolated
    sys.exit(_main(sys.argv[1:]))
