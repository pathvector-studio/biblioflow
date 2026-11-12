"""The CSL formatter: escaping, the isolated child process and its limits."""

import asyncio
import sys
import time

import pytest

from app.services.csl import STYLES, BibliographyError, format_entries
from app.services.csl import isolated
from app.services.csl.isolated import format_entries_isolated


def item(i: int = 0, **kw):
    base = {
        "id": f"k{i}", "type": "article-journal", "title": f"Paper number {i} about routing",
        "author": [{"family": f"Fam{i % 97}", "given": f"Given{j}"} for j in range(3)],
        "issued": {"date-parts": [[1990 + i % 35]]}, "container-title": "Journal X", "DOI": f"10.1/{i}",
    }
    base.update(kw)
    return base


def test_styles_listing():
    assert {s.id: s.kind for s in STYLES.values()} == {
        "apa": "author-date", "chicago-author-date": "author-date", "ieee": "numeric", "nature": "numeric",
        "vancouver": "numeric", "acm-sig-proceedings": "numeric",
    }


@pytest.mark.parametrize("style", list(STYLES))
def test_html_escapes_every_field_value(style):
    evil = '<script>alert(1)</script> & "q" <img src=x onerror=alert(2)>'
    it = item(
        title=evil, **{"container-title": evil, "publisher": evil, "publisher-place": evil, "URL": "https://x.example/?a=1&b=<2>"},
        author=[{"family": evil, "given": evil}], type="chapter", editor=[{"family": evil, "given": evil}],
    )
    [html_out] = format_entries([it], style, "html")
    assert "<script" not in html_out and "<img" not in html_out and "onerror=alert(2)>" not in html_out
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html_out
    # the only tags are the ones the style itself adds
    import re

    assert set(re.findall(r"</?([a-z]+)", html_out)) <= {"i", "b", "sup", "sub", "span", "div"}
    [text_out] = format_entries([it], style, "text")
    assert "<script>alert(1)</script>" in text_out and "&lt;" not in text_out


def test_html_ampersand_and_quotes():
    [out] = format_entries([item(title="Routing & policy")], "apa", "html")
    assert "Routing &amp; policy" in out
    [out] = format_entries([item(title="Routing & policy")], "apa", "text")
    assert "Routing & policy" in out


def test_unknown_style_locale_format():
    with pytest.raises(BibliographyError, match="style"):
        format_entries([item()], "nope")
    with pytest.raises(BibliographyError, match="locale"):
        format_entries([item()], "apa", locale="fr-FR")
    with pytest.raises(BibliographyError, match="format"):
        format_entries([item()], "apa", "docx")  # type: ignore[arg-type]
    assert format_entries([], "apa") == []


def test_malformed_items_are_a_bibliography_error():
    with pytest.raises(BibliographyError):
        format_entries([{"id": "x"}], "apa")  # no type
    with pytest.raises(BibliographyError):
        format_entries([item(author="not a list")], "apa")


async def test_isolated_gives_the_same_result():
    items = [item(i) for i in range(5)]
    for style in STYLES:
        assert await format_entries_isolated(items, style, "html", "ja-JP") == format_entries(items, style, "html", "ja-JP")


async def test_isolated_reports_bad_input():
    with pytest.raises(BibliographyError, match="Unknown style"):
        await format_entries_isolated([item()], "nope")


async def test_numeric_style_numbers_in_list_order_and_author_date_sorts():
    items = [item(1, author=[{"family": "Zed"}]), item(2, author=[{"family": "Abe"}])]
    ieee = await format_entries_isolated(items, "ieee")
    assert ieee[0].startswith("[1] Z") and ieee[1].startswith("[2] A")
    apa = await format_entries_isolated(items, "apa")
    assert apa[0].startswith("Abe") and apa[1].startswith("Zed")


async def test_thousand_papers_in_apa_within_ten_seconds():
    items = [item(i) for i in range(1000)]
    t = time.monotonic()
    out = await format_entries_isolated(items, "apa", "html")
    elapsed = time.monotonic() - t
    assert len(out) == 1000 and elapsed < 10, elapsed


async def test_a_hostile_author_list_hits_the_memory_limit_and_the_next_call_works():
    # 100,000 authors on one record. citeproc-py prints only the first few authors, so with the production limit
    # (512 MiB) this is formatted in well under a second; with a small limit the child is stopped by it.
    hostile = item(0, author=[{"family": f"Family{n}", "given": f"Given{n}"} for n in range(100_000)])
    assert len(await format_entries_isolated([hostile], "apa", "html")) == 1
    small = 64 * 1024 * 1024
    with pytest.raises(BibliographyError, match="too large or too complex"):
        await format_entries_isolated([hostile], "apa", "html", memory_bytes=small)
    assert len(await format_entries_isolated([item(1)], "apa", memory_bytes=small)) == 1  # a normal request fits


async def test_real_formatting_work_that_outlasts_the_time_limit_is_killed():
    # 1,000 papers by one author in one year: Chicago has to disambiguate them, which takes ~10 s.
    items = [item(i, author=[{"family": "Same", "given": "Person"}], issued={"date-parts": [[2020]]}) for i in range(1000)]
    t = time.monotonic()
    with pytest.raises(BibliographyError, match="too long"):
        await format_entries_isolated(items, "chicago-author-date", "html", timeout=2)
    assert time.monotonic() - t < 8


async def test_slow_child_is_killed(monkeypatch):
    async def spawn(*args, **kwargs):
        return await real(sys.executable, "-c", "import time; time.sleep(60)", **kwargs)

    real = isolated.asyncio.create_subprocess_exec
    monkeypatch.setattr(isolated.asyncio, "create_subprocess_exec", spawn)
    t = time.monotonic()
    with pytest.raises(BibliographyError, match="too long"):
        await format_entries_isolated([item()], "apa", timeout=1)
    assert time.monotonic() - t < 10


async def test_crashing_child_is_a_bibliography_error(monkeypatch):
    async def spawn(*args, **kwargs):
        return await real(sys.executable, "-c", "import sys; sys.exit(137)", **kwargs)

    real = isolated.asyncio.create_subprocess_exec
    monkeypatch.setattr(isolated.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(BibliographyError, match="could not be formatted"):
        await format_entries_isolated([item()], "apa")


async def test_child_environment_has_no_secrets(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", "must-not-reach-the-child")
    captured = {}
    real = isolated.asyncio.create_subprocess_exec

    async def spy(*args, **kwargs):
        captured.update(kwargs["env"])
        return await real(*args, **kwargs)

    monkeypatch.setattr(isolated.asyncio, "create_subprocess_exec", spy)
    await format_entries_isolated([item()], "apa")
    assert set(captured) == {"PATH", "PYTHONPATH", "LANG", "PYTHONDONTWRITEBYTECODE"}


def test_concurrent_calls_do_not_interfere():
    async def run():
        return await asyncio.gather(*[format_entries_isolated([item(i)], "ieee") for i in range(4)])

    out = asyncio.run(run())
    assert all(o[0].startswith("[1] ") for o in out)
