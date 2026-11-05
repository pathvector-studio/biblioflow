"""Minimal valid PDF builder (one Helvetica text line per page) with a correct xref table."""

from __future__ import annotations


def _esc(s: str) -> str:
    return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(text_lines: list[str] | None = None, *, title: str | None = None, pages: int = 1, nonce: str = "") -> bytes:
    """Return PDF bytes whose first page contains ``text_lines``. ``nonce`` changes the bytes (and sha256)."""
    lines = text_lines or ["Hello BiblioFlow"]
    objs: dict[int, bytes] = {}
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(pages))
    objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode()
    objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    next_id = 4
    for i in range(pages):
        page_lines = lines if i == 0 else [f"page {i + 1}"]
        ops = ["BT", "/F1 12 Tf", "14 TL", "50 750 Td"]
        for ln in page_lines:
            ops.append(f"({_esc(ln)}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        objs[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>"
        ).encode()
        objs[content_id] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    info_id = next_id
    info = "<< "
    if title:
        info += f"/Title ({_esc(title)}) "
    if nonce:
        info += f"/Keywords ({_esc(nonce)}) "
    info += ">>"
    objs[info_id] = info.encode("latin-1")

    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {info_id + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for num in range(1, info_id + 1):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {info_id + 1} /Root 1 0 R /Info {info_id} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


_HELV = {c: w for c, w in zip("abcdefghijklmnopqrstuvwxyz", (556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500, 278, 556, 500, 722, 500, 500, 500), strict=True)}  # fmt: skip


def make_pdf_glyph_line(words: list[str], *, size: float = 10, tracking: float = 0.4, word_gap: float = 2.5) -> bytes:
    """One line drawn glyph by glyph, each with its own ``Tm`` (like a browser's "print to PDF").

    Glyphs advance by their Helvetica width plus ``tracking`` points; words are separated by
    ``word_gap`` extra points and no space character is drawn.
    """
    ops = ["BT"]
    x = 50.0
    for w in words:
        for ch in w:
            ops.append(f"/F1 {size} Tf 1 0 0 1 {x:.2f} 742 Tm ({_esc(ch)}) Tj")
            x += _HELV.get(ch, 556) / 1000 * size + tracking
        x += word_gap
    ops.append("ET")
    stream = "\n".join(ops).encode("latin-1")
    objs = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [4 0 R] /Count 1 >>",
        3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        4: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 5 0 R /Resources << /Font << /F1 3 0 R >> >> >>",
        5: b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    }
    out = bytearray(b"%PDF-1.4\n")
    offsets = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n" + b"".join(f"{offsets[n]:010d} 00000 n \n".encode() for n in range(1, 6))
    out += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)


def make_pdf_pages(pages: list[list[tuple[float, str]]], *, width: int = 612, height: int = 792) -> bytes:
    """PDF with explicit pages of ``(font_size, text)`` lines, laid out top-down from y = height - 50.

    Each line is drawn at ``x = 50`` with an absolute ``Tm`` so tests know the exact coordinates:
    the baseline of line ``i`` (0-based) on a page is ``height - 50 - 20 * i``.
    """
    objs: dict[int, bytes] = {1: b"<< /Type /Catalog /Pages 2 0 R >>"}
    kids = " ".join(f"{4 + 2 * i} 0 R" for i in range(len(pages)))
    objs[2] = f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode()
    objs[3] = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    next_id = 4
    for lines in pages:
        ops = ["BT"]
        for i, (size, text) in enumerate(lines):
            ops.append(f"/F1 {size} Tf 1 0 0 1 50 {height - 50 - 20 * i} Tm ({_esc(text)}) Tj")
        ops.append("ET")
        stream = "\n".join(ops).encode("latin-1")
        page_id, content_id = next_id, next_id + 1
        next_id += 2
        objs[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Contents {content_id} 0 R "
            f"/Resources << /Font << /F1 3 0 R >> >> >>"
        ).encode()
        objs[content_id] = b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream"
    out = bytearray(b"%PDF-1.4\n")
    offsets: dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += f"{num} 0 obj\n".encode() + objs[num] + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {next_id}\n".encode() + b"0000000000 65535 f \n"
    for num in range(1, next_id):
        out += f"{offsets[num]:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {next_id} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
