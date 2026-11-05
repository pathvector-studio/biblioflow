"""Chunk extraction: lines, coordinates, headings, letter-spaced text, isolation."""

import sys

import pytest

from app.services import pdf
from app.services.chunking import Line, chunk_pages, extract_chunks
from app.services.pdf import InvalidPdf, extract_chunks_isolated
from tests.pdfgen import make_pdf_glyph_line, make_pdf_pages


def _write(tmp_path, pages, **kw):
    f = tmp_path / "a.pdf"
    f.write_bytes(make_pdf_pages(pages, **kw))
    return f


def test_lines_coordinates_and_headings(tmp_path):
    f = _write(
        tmp_path,
        [[(20, "1 Introduction"), (10, "BGP is widely used in data centers."), (10, "It scales well.")]],
    )
    res = extract_chunks(f)
    assert res.page_count == 1 and len(res.chunks) == 1
    c = res.chunks[0]
    assert c.page_number == 1
    assert c.section == "1 Introduction"
    assert c.text.splitlines() == ["1 Introduction", "BGP is widely used in data centers.", "It scales well."]
    assert len(c.bboxes) == 3
    first, second = c.bboxes[0], c.bboxes[1]
    # Origin is bottom-left: line 0 has baseline y = 792 - 50, size 20 -> y1 = 742 - 4, y2 = 742 + 16.
    assert first["x1"] == pytest.approx(50)
    assert first["y1"] == pytest.approx(738, abs=0.1) and first["y2"] == pytest.approx(758, abs=0.1)
    assert first["width"] == 612 and first["height"] == 792
    assert first["x2"] > first["x1"]
    assert second["y2"] < first["y1"]  # next line sits lower on the page


def test_no_heading_when_sizes_equal(tmp_path):
    res = extract_chunks(_write(tmp_path, [[(10, "alpha beta"), (10, "gamma delta")]]))
    assert res.chunks[0].section is None


def test_letter_spaced_text_is_compacted(tmp_path):
    res = extract_chunks(_write(tmp_path, [[(10, "B G P r o u t i n g f a b r i c s")]]))
    assert res.chunks[0].text == "BGProutingfabrics"


def test_glyph_by_glyph_line_gets_word_spaces_from_geometry(tmp_path):
    # "print to PDF" output: one glyph per Tm, no space characters, words separated only by position.
    words = ["the", "load", "is", "powered", "by", "the", "backbone", "network"]
    f = tmp_path / "g.pdf"
    f.write_bytes(make_pdf_glyph_line(words))
    assert extract_chunks(f).chunks[0].text == " ".join(words)


def test_glyph_by_glyph_line_without_word_gaps_stays_one_word(tmp_path):
    f = tmp_path / "g.pdf"
    f.write_bytes(make_pdf_glyph_line(["backbone", "network"], word_gap=0))
    assert extract_chunks(f).chunks[0].text == "backbonenetwork"


def test_chunks_do_not_span_pages_and_overlap_one_line():
    lines = [Line(f"line number {i:02d} " + "x" * 40, 50, 700 - 12 * i, 300, 710 - 12 * i, 10) for i in range(30)]
    chunks = chunk_pages([(612, 792, lines), (612, 792, lines[:3])], target=300)
    page1 = [c for c in chunks if c.page_number == 1]
    page2 = [c for c in chunks if c.page_number == 2]
    assert len(page1) > 1 and len(page2) == 1
    for a, b in zip(page1, page1[1:]):
        assert a.text.splitlines()[-1] == b.text.splitlines()[0]  # overlap line
    assert all(len(c.text) <= 300 + 120 for c in chunks)
    assert page2[0].text.splitlines()[0] == lines[0].text  # no carry-over across pages
    covered = {ln for c in page1 for ln in c.text.splitlines()}
    assert covered == {ln.text for ln in lines}


def test_section_carries_over_pages():
    head = Line("2 Methods", 50, 700, 150, 720, 20)
    body = [Line("some body text here", 50, 600, 200, 610, 10)] * 5
    chunks = chunk_pages([(612, 792, [head, *body]), (612, 792, body)])
    assert [c.section for c in chunks] == ["2 Methods", "2 Methods"]


def test_heading_detection():
    body = [Line("plain body text of the paper goes here", 50, 600, 300, 610, 10)] * 20

    def sections(*lines):
        return {c.section for c in chunk_pages([(612, 792, [*lines, *body])])}

    # Author line set larger than the body: several commas, not a heading.
    assert sections(Line("Zefeng Zhu a, Liang Liang a, Shunbo Lei b", 50, 700, 300, 712, 12)) == {None}
    # Numbered and well-known titles at body size are headings.
    assert sections(Line("4.4. Case 4 Hypothetical failure", 50, 700, 300, 710, 10)) == {"4.4. Case 4 Hypothetical failure"}
    assert sections(Line("Abstract", 50, 700, 100, 710, 10)) == {"Abstract"}
    assert sections(Line("参考文献", 50, 700, 100, 710, 10)) == {"参考文献"}
    # A sentence that starts with a number is not.
    assert sections(Line("1 indicates that the load is powered", 50, 700, 300, 710, 10)) == {None}


def test_reference_entries_are_not_headings():
    body = [Line("plain body text of the paper goes here", 50, 600, 300, 610, 10)] * 3
    refs = [Line("References", 50, 700, 120, 710, 10), Line("2. Smith J. A study of BGP", 50, 690, 300, 700, 10), *body]
    assert {c.section for c in chunk_pages([(612, 792, refs)])} == {"References"}


def test_overlong_line_is_split():
    chunks = chunk_pages([(612, 792, [Line("a" * 2500, 50, 700, 500, 710, 10)])], target=1000)
    assert [len(c.text) for c in chunks][0] <= 1000 and len(chunks) >= 3


def test_garbage_pdf_is_invalid(tmp_path):
    f = tmp_path / "bad.pdf"
    f.write_bytes(b"%PDF-1.7\nnope")
    with pytest.raises(InvalidPdf):
        extract_chunks(f)


async def test_extract_in_child(tmp_path):
    f = _write(tmp_path, [[(10, "hello world")], [(10, "second page")]])
    res = await extract_chunks_isolated(f)
    assert res.page_count == 2
    assert [c.page_number for c in res.chunks] == [1, 2]
    assert res.chunks[1].text == "second page"


async def test_chunk_child_is_killed_on_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "_chunks_command", lambda p: [sys.executable, "-c", "import time; time.sleep(30)"])
    with pytest.raises(InvalidPdf, match="too long"):
        await extract_chunks_isolated(tmp_path / "x.pdf", timeout=0.5)


async def test_chunk_child_gets_no_secrets(tmp_path, monkeypatch):
    script = "import json, os; print(json.dumps({'error': ','.join(sorted(os.environ))}))"
    monkeypatch.setattr(pdf, "_chunks_command", lambda p: [sys.executable, "-c", script])
    with pytest.raises(InvalidPdf) as exc:
        await extract_chunks_isolated(tmp_path / "x.pdf")
    assert not {"JWT_SECRET", "DATABASE_URL", "REDIS_URL"} & set(str(exc.value).split(","))
