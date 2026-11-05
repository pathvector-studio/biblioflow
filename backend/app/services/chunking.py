"""Page text -> search chunks with coordinates (pypdf only).

Coordinates are PDF points with the origin at the bottom-left of the page (``y1`` < ``y2``). Every bbox also
carries the page ``width`` / ``height`` so a client can convert to any viewport scale. Rectangles are derived
from the text matrix and font size, and the right edge of a run is estimated from the character count (pypdf
does not expose glyph advances), so they are accurate to within a fraction of a character.
"""

from __future__ import annotations

import logging
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from app.services.metadata.identifiers import compact_letter_spacing

logger = logging.getLogger("biblioflow.chunking")

CHUNK_TARGET_CHARS = 1200
MAX_CHUNK_PAGES = 2000
HEADING_RATIO = 1.2
HEADING_MAX_CHARS = 120
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_WIDE_START = 0x2E80  # CJK and other full-width scripts are about one em wide
# Helvetica advance widths (1/1000 em) for ASCII 32..126. Only used to tell word gaps from letter gaps in
# lines drawn one glyph at a time (browser "print to PDF"), where the text carries no reliable spaces.
_HELVETICA = (
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278, 556, 556, 556, 556, 556, 556,
    556, 556, 556, 556, 278, 278, 584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667,
    556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944, 667, 667, 611, 278, 278, 278, 469, 556, 333, 556,
    556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722,
    500, 500, 500, 334, 260, 334, 584,
)  # fmt: skip
GLYPH_RUN_MIN = 6  # a line with at least this many runs, mostly single glyphs, is drawn glyph by glyph
GLYPH_RUN_SHARE = 0.6
WORD_GAP_EM = 0.08  # extra space beyond the expected advance that separates two words


@dataclass
class Line:
    text: str
    x1: float
    y1: float
    x2: float
    y2: float
    size: float


@dataclass
class Chunk:
    page_number: int
    section: str | None
    text: str
    bboxes: list[dict[str, float]] = field(default_factory=list)


@dataclass
class ExtractedChunks:
    page_count: int
    chunks: list[Chunk]


def _mul(a: list[float], b: list[float]) -> list[float]:
    """Row-vector convention (PDF): ``a`` applied first, then ``b``."""
    return [
        a[0] * b[0] + a[1] * b[2], a[0] * b[1] + a[1] * b[3],
        a[2] * b[0] + a[3] * b[2], a[2] * b[1] + a[3] * b[3],
        a[4] * b[0] + a[5] * b[2] + b[4], a[4] * b[1] + a[5] * b[3] + b[5],
    ]  # fmt: skip


def _advance(text: str, size: float) -> float:
    return sum(size if ord(c) >= _WIDE_START else size * 0.5 for c in text)


@dataclass
class _Run:
    text: str
    x: float
    y: float
    size: float
    end: float


def page_lines(page: Any) -> list[Line]:
    """Group the text runs of one page into lines (content-stream order, then merged by baseline)."""
    runs: list[_Run] = []

    def visitor(text: str, cm: list[float], tm: list[float], _font: Any, font_size: float) -> None:
        text = text.replace("\n", " ").replace("\r", " ")
        if not text.strip():
            return
        try:
            m = _mul([float(v) for v in tm], [float(v) for v in cm])
            scale = abs(m[0] * m[3] - m[1] * m[2]) ** 0.5
            size = abs(float(font_size or 0)) * scale
        except (TypeError, ValueError):
            return
        if size <= 0.1:
            return
        runs.append(_Run(text, m[4], m[5], size, m[4] + _advance(text, size)))

    try:
        page.extract_text(visitor_text=visitor)
    except RecursionError:
        raise
    except Exception:  # noqa: BLE001 - a broken page must not abort the whole document
        logger.debug("text extraction failed on a page", exc_info=True)

    lines: list[list[_Run]] = []
    for run in runs:
        if lines:
            cur = lines[-1]
            last = cur[-1]
            same_line = abs(run.y - last.y) <= 0.4 * max(run.size, last.size)
            progressing = run.x >= last.x - 0.5 * last.size  # a jump back to the left starts a new line
            if same_line and progressing:
                cur.append(run)
                continue
        lines.append([run])
    return [ln for ln in (_to_line(r) for r in lines) if ln is not None]


def _glyph_width(ch: str) -> float:
    o = ord(ch)
    if 32 <= o < 127:
        return _HELVETICA[o - 32] / 1000
    return 1.0 if o >= _WIDE_START else 0.55


def _glyph_line_text(runs: list[_Run]) -> str | None:
    """Text of a line drawn one glyph per run, with spaces placed from the geometry (None: not such a line).

    The spaces pypdf reports between those runs come from letter spacing, so they are dropped. The
    advance of each run is estimated from Helvetica widths, scaled by the line's median ratio of
    observed to estimated advance (this absorbs the real font and the tracking); a run that starts
    more than ``WORD_GAP_EM`` further right than expected begins a new word.
    """
    texts = [r.text.strip() for r in runs]
    if len(runs) < GLYPH_RUN_MIN or sum(len(t) == 1 for t in texts) < GLYPH_RUN_SHARE * len(runs):
        return None
    expected = [sum(_glyph_width(c) for c in t) * r.size for r, t in zip(runs, texts, strict=True)]
    ratios = [
        (b.x - a.x) / e for a, b, e in zip(runs, runs[1:], expected, strict=False) if b.x > a.x and e > 0
    ]
    if not ratios:
        return None
    scale = statistics.median(ratios)
    parts = [texts[0]]
    for a, b, e, t in zip(runs, runs[1:], expected, texts[1:], strict=False):
        if b.x - a.x - scale * e > WORD_GAP_EM * a.size * scale:
            parts.append(" ")
        parts.append(t)
    return "".join(parts)


def _to_line(runs: list[_Run]) -> Line | None:
    glyph_text = _glyph_line_text(runs)
    if glyph_text is not None:
        text = " ".join(glyph_text.split())
    else:
        parts: list[str] = []
        prev: _Run | None = None
        for run in runs:
            if prev is not None:
                gap = run.x - prev.end
                glued = prev.text[-1:].isspace() or run.text[:1].isspace()
                if not glued and gap > 0.15 * run.size:
                    parts.append(" ")
            parts.append(run.text)
            prev = run
        text = " ".join(compact_letter_spacing("".join(parts)).split())
    text = _CONTROL.sub("", text)
    if not text:
        return None
    size = max(r.size for r in runs)
    base = runs[0].y
    return Line(text, min(r.x for r in runs), base - 0.2 * size, max(r.end for r in runs), base + 0.8 * size, size)


def _body_size(lines_by_page: list[list[Line]]) -> float:
    sizes: list[float] = []
    for lines in lines_by_page:
        for ln in lines:
            sizes.extend([round(ln.size, 1)] * min(len(ln.text), 200))
    return statistics.median(sizes) if sizes else 0.0


_NUMBERED_HEADING = re.compile(r"^(?:\d{1,2}(?:\.\d{1,2}){0,3}\.?|[IVX]{1,5}\.|[A-H]\.)\s+[A-Z\u3040-\u30ff\u4e00-\u9fff]")
_KNOWN_HEADINGS = {
    "abstract", "introduction", "background", "related work", "method", "methods", "methodology", "results",
    "discussion", "conclusion", "conclusions", "acknowledgment", "acknowledgments", "acknowledgement",
    "acknowledgements", "references", "bibliography", "appendix",
    "概要", "要旨", "はじめに", "序論", "まとめ", "結論", "おわりに", "謝辞", "参考文献",
}  # fmt: skip
_REFERENCES = {"references", "bibliography", "参考文献"}


def _heading_key(text: str) -> str:
    return re.sub(r"^[\dIVX.\s]+(?=\S)|[:.\s]+$", "", text).strip().lower()


def _is_heading(line: Line, body: float) -> bool:
    """A short line that looks like a section title.

    Numbered ("3.2 Results", "IV. Method") and well-known ("Abstract", "References", "参考文献") titles are
    accepted at body size or larger (journals often set them in bold body size). Other lines must be set
    clearly larger than the body; author and affiliation lines (several commas) are not headings.
    """
    text = line.text
    if body <= 0 or len(text) > HEADING_MAX_CHARS or line.size < 0.95 * body:
        return False
    if _heading_key(text) in _KNOWN_HEADINGS:
        return True
    if _NUMBERED_HEADING.match(text) and len(text.split()) <= 12 and not text.rstrip().endswith((",", ";")):
        return True
    return line.size >= HEADING_RATIO * body and text.count(",") < 2


def _bbox(line: Line, width: float, height: float) -> dict[str, float]:
    x1, x2 = max(0.0, min(line.x1, width)), max(0.0, min(line.x2, width))
    y1, y2 = max(0.0, min(line.y1, height)), max(0.0, min(line.y2, height))
    return {
        "x1": round(x1, 2), "y1": round(y1, 2), "x2": round(x2, 2), "y2": round(y2, 2),
        "width": round(width, 2), "height": round(height, 2),
    }  # fmt: skip


def chunk_pages(
    pages: list[tuple[float, float, list[Line]]], target: int = CHUNK_TARGET_CHARS
) -> list[Chunk]:
    """``pages`` is ``[(width, height, lines)]`` in page order. Chunks never span pages; the last line of a
    chunk is repeated at the start of the next one on the same page."""
    body = _body_size([p[2] for p in pages])
    chunks: list[Chunk] = []
    section: str | None = None
    for number, (width, height, lines) in enumerate(pages, start=1):
        cur: list[Line] = []
        cur_len = 0
        cur_section = section
        fresh = 0  # lines in `cur` that are new (not the overlap line)

        def flush() -> None:
            if fresh:
                chunks.append(
                    Chunk(number, cur_section, "\n".join(ln.text for ln in cur), [_bbox(ln, width, height) for ln in cur])
                )

        for ln in lines:
            # Inside the reference list numbered entries ("12. Smith J, ...") are not headings.
            in_references = section is not None and _heading_key(section) in _REFERENCES
            if _is_heading(ln, body) and (not in_references or _heading_key(ln.text) in _KNOWN_HEADINGS):
                section = ln.text
            pieces = (
                [ln]
                if len(ln.text) <= target
                else [Line(ln.text[i : i + target], ln.x1, ln.y1, ln.x2, ln.y2, ln.size) for i in range(0, len(ln.text), target)]
            )
            for piece in pieces:
                if cur and cur_len + len(piece.text) + 1 > target and fresh:
                    flush()
                    cur = [cur[-1]]
                    cur_len = len(cur[0].text)
                    fresh = 0
                if fresh == 0:
                    cur_section = section
                cur.append(piece)
                cur_len += len(piece.text) + 1
                fresh += 1
        flush()
    return chunks


def extract_chunks(path: str | Path) -> ExtractedChunks:
    """Read ``path`` and return its chunks. Raises ``InvalidPdf`` like ``inspect_pdf``. Run it in the child."""
    from app.services.pdf import InvalidPdf, MAX_PAGES  # noqa: PLC0415 - pdf imports this module lazily

    try:
        with open(path, "rb") as fh:
            reader = PdfReader(fh, strict=False)
            if reader.is_encrypted:
                try:
                    ok = reader.decrypt("")
                except Exception as exc:  # noqa: BLE001
                    raise InvalidPdf("encrypted PDF") from exc
                if not ok:
                    raise InvalidPdf("password-protected PDF")
            count = len(reader.pages)
            if count < 1:
                raise InvalidPdf("PDF has no pages")
            if count > min(MAX_PAGES, MAX_CHUNK_PAGES):
                raise InvalidPdf("PDF has too many pages to index")
            pages: list[tuple[float, float, list[Line]]] = []
            for page in reader.pages:
                try:
                    box = page.mediabox
                    width, height = abs(float(box.width)), abs(float(box.height))
                except Exception:  # noqa: BLE001
                    width, height = 612.0, 792.0
                pages.append((width, height, page_lines(page)))
            return ExtractedChunks(page_count=count, chunks=chunk_pages(pages))
    except InvalidPdf:
        raise
    except RecursionError as exc:
        raise InvalidPdf("PDF structure too deep") from exc
    except Exception as exc:  # noqa: BLE001
        raise InvalidPdf("unreadable PDF") from exc
