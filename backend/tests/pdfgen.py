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
