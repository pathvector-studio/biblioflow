"""PDF validation and light metadata extraction (pypdf). Functions are synchronous; use ``asyncio.to_thread``."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

from app.services.metadata.identifiers import find_identifiers_in_text

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


def has_pdf_magic(head: bytes) -> bool:
    return b"%PDF-" in head[:PDF_MAGIC_WINDOW]


def _clean_title(raw: object) -> str | None:
    if not isinstance(raw, str):
        return None
    title = " ".join(raw.replace("\x00", " ").split())[:MAX_TITLE_LEN]
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
            try:
                meta = reader.metadata
                title = _clean_title(meta.title) if meta is not None else None
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
            doi, arxiv_id = find_identifiers_in_text("\n".join(chunks))
            return PdfInfo(page_count=page_count, title=title, doi=doi, arxiv_id=arxiv_id, first_page_text=first_page_text)
    except InvalidPdf:
        raise
    except RecursionError as exc:
        raise InvalidPdf("PDF structure too deep") from exc
    except Exception as exc:  # noqa: BLE001 - pypdf raises a wide variety of errors on bad input
        raise InvalidPdf("unreadable PDF") from exc
