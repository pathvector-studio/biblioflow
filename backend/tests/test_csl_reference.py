"""citeproc-py output against citeproc-js (the engine Zotero uses) for the bundled styles.

``fixtures/csl/expected.json`` is produced by ``scripts/gen-csl-expected.mjs`` (citeproc-js is a dev-only tool run
from a scratch install; it is not a dependency). Every difference that remains is listed in ``ALLOWED`` with the
reason it cannot be fixed by pre-processing. The test fails when an unlisted difference appears *and* when a listed
one disappears, so the list can only shrink deliberately.
"""

import html
import itertools
import json
import re
from pathlib import Path

import pytest

from app.services.csl import LOCALES, STYLES, format_entries

FIX = Path(__file__).parent / "fixtures" / "csl"
ITEMS = json.loads((FIX / "items.json").read_text(encoding="utf-8"))
EXPECTED = json.loads((FIX / "expected.json").read_text(encoding="utf-8"))

# ---------------------------------------------------------------- the differences we accept
# Each reason is a normalization applied to both outputs; an allowed difference has to vanish under its reasons.
REASONS = {
    # citeproc-py ignores text-case="capitalize-first" on a names label: "(eds.)" where the style asks for "(Eds.)".
    "label-case": lambda s: s.replace("(eds.)", "(Eds.)"),
    # In citeproc-py a <group> that holds a literal value and a macro whose variables are empty is not suppressed:
    # APA prints "(Publication)" for a thesis that has a genre but no report number.
    "empty-group": lambda s: s.replace(" (Publication)", ""),
    # citeproc-js joins the Japanese "and" / "et al." terms to the neighbouring Latin text without a space (or a
    # comma before it) and does not italicize "ほか"; citeproc-py keeps the style's delimiters and markup.
    "ja-spacing": lambda s: re.sub(r"\s*,?\s*(と|ほか)\s*", r"\1", s.replace("<i>ほか</i>", "ほか")),
    # citeproc-js applies title case only when the language is English; citeproc-py title-cases every title.
    "title-case": lambda s: s.lower(),
    # citeproc-js never italicizes CJK text, citeproc-py follows the style literally.
    "cjk-italic": lambda s: re.sub(r"<i>([^<]*[぀-鿿][^<]*)</i>", r"\1", s),
}

ALLOWED: dict[tuple[str, str, str, str], tuple[str, ...]] = {
    # (format, style, locale, item id): reasons
    ('html', 'acm-sig-proceedings', 'en-US', 'yamada2022japanese'): ('cjk-italic',),
    ('html', 'acm-sig-proceedings', 'ja-JP', 'brown2018memory'): ('ja-spacing',),
    ('html', 'acm-sig-proceedings', 'ja-JP', 'garcia2024attention'): ('ja-spacing',),
    ('html', 'acm-sig-proceedings', 'ja-JP', 'lee2019congestion'): ('ja-spacing',),
    ('html', 'acm-sig-proceedings', 'ja-JP', 'unknown-year'): ('ja-spacing',),
    ('html', 'acm-sig-proceedings', 'ja-JP', 'yamada2022japanese'): ('cjk-italic',),
    ('html', 'apa', 'en-US', 'brown2018memory'): ('label-case',),
    ('html', 'apa', 'en-US', 'nguyen2016thesis'): ('empty-group',),
    ('html', 'apa', 'ja-JP', 'nguyen2016thesis'): ('empty-group',),
    ('html', 'chicago-author-date', 'en-US', 'yamada2022japanese'): ('cjk-italic',),
    ('html', 'chicago-author-date', 'ja-JP', 'brown2018memory'): ('ja-spacing', 'title-case'),
    ('html', 'chicago-author-date', 'ja-JP', 'garcia2024attention'): ('title-case',),
    ('html', 'chicago-author-date', 'ja-JP', 'lee2019congestion'): ('ja-spacing', 'title-case'),
    ('html', 'chicago-author-date', 'ja-JP', 'nguyen2016thesis'): ('title-case',),
    ('html', 'chicago-author-date', 'ja-JP', 'smith2021routing'): ('title-case',),
    ('html', 'chicago-author-date', 'ja-JP', 'unknown-year'): ('ja-spacing', 'title-case'),
    ('html', 'chicago-author-date', 'ja-JP', 'yamada2022japanese'): ('cjk-italic',),
    ('html', 'ieee', 'en-US', 'yamada2022japanese'): ('cjk-italic',),
    ('html', 'ieee', 'ja-JP', 'brown2018memory'): ('ja-spacing',),
    ('html', 'ieee', 'ja-JP', 'garcia2024attention'): ('ja-spacing',),
    ('html', 'ieee', 'ja-JP', 'lee2019congestion'): ('ja-spacing',),
    ('html', 'ieee', 'ja-JP', 'unknown-year'): ('ja-spacing',),
    ('html', 'ieee', 'ja-JP', 'yamada2022japanese'): ('cjk-italic',),
    ('html', 'nature', 'en-US', 'yamada2022japanese'): ('cjk-italic',),
    ('html', 'nature', 'ja-JP', 'garcia2024attention'): ('ja-spacing',),
    ('html', 'nature', 'ja-JP', 'yamada2022japanese'): ('cjk-italic',),
    ('text', 'acm-sig-proceedings', 'ja-JP', 'brown2018memory'): ('ja-spacing',),
    ('text', 'acm-sig-proceedings', 'ja-JP', 'garcia2024attention'): ('ja-spacing',),
    ('text', 'acm-sig-proceedings', 'ja-JP', 'lee2019congestion'): ('ja-spacing',),
    ('text', 'acm-sig-proceedings', 'ja-JP', 'unknown-year'): ('ja-spacing',),
    ('text', 'apa', 'en-US', 'brown2018memory'): ('label-case',),
    ('text', 'apa', 'en-US', 'nguyen2016thesis'): ('empty-group',),
    ('text', 'apa', 'ja-JP', 'nguyen2016thesis'): ('empty-group',),
    ('text', 'chicago-author-date', 'ja-JP', 'brown2018memory'): ('ja-spacing', 'title-case'),
    ('text', 'chicago-author-date', 'ja-JP', 'garcia2024attention'): ('title-case',),
    ('text', 'chicago-author-date', 'ja-JP', 'lee2019congestion'): ('ja-spacing', 'title-case'),
    ('text', 'chicago-author-date', 'ja-JP', 'nguyen2016thesis'): ('title-case',),
    ('text', 'chicago-author-date', 'ja-JP', 'smith2021routing'): ('title-case',),
    ('text', 'chicago-author-date', 'ja-JP', 'unknown-year'): ('ja-spacing', 'title-case'),
    ('text', 'ieee', 'ja-JP', 'brown2018memory'): ('ja-spacing',),
    ('text', 'ieee', 'ja-JP', 'garcia2024attention'): ('ja-spacing',),
    ('text', 'ieee', 'ja-JP', 'lee2019congestion'): ('ja-spacing',),
    ('text', 'ieee', 'ja-JP', 'unknown-year'): ('ja-spacing',),
    ('text', 'nature', 'ja-JP', 'garcia2024attention'): ('ja-spacing',),
}


def _norm_js(fmt: str, out: str) -> str:
    if fmt == "html":
        out = out.replace('</div><div class="csl-right-inline">', " ")
        out = re.sub(r"</?div[^>]*>", "", out)
    return " ".join(html.unescape(out).split())


def _norm_py(fmt: str, out: str) -> str:
    return " ".join((html.unescape(out) if fmt == "html" else out).split())


def _differences() -> dict[tuple[str, str, str, str], tuple[str, str]]:
    found = {}
    for fmt, style, locale in itertools.product(("text", "html"), STYLES, LOCALES):
        ref = EXPECTED["cases"][style][locale][fmt]
        got = format_entries(ITEMS, style, fmt, locale)
        assert len(got) == len(ref) == len(ITEMS)
        for mine, theirs in zip(got, ref):
            a, b = _norm_py(fmt, mine), _norm_js(fmt, theirs["out"])
            if a != b:
                found[(fmt, style, locale, theirs["id"])] = (a, b)
    return found


def test_fixture_is_current():
    """The expected file was generated for exactly these items, styles and locales."""
    assert EXPECTED["engine"].startswith("citeproc-js ")
    assert set(EXPECTED["cases"]) == set(STYLES)
    assert len(ITEMS) == 8
    for style in STYLES:
        for locale in LOCALES:
            for fmt in ("text", "html"):
                ref = EXPECTED["cases"][style][locale][fmt]
                assert sorted(r["id"] for r in ref) == sorted(i["id"] for i in ITEMS)


def test_entries_come_in_the_reference_order():
    """Citation-order styles keep the order of the items; the others sort as the style says (ACM SIG sorts by author)."""
    for style, locale in itertools.product(STYLES, LOCALES):
        ref = EXPECTED["cases"][style][locale]["text"]
        texts = format_entries(ITEMS, style, "text", locale)
        if style in ("ieee", "nature", "vancouver"):
            assert [r["id"] for r in ref] == [i["id"] for i in ITEMS]
            assert [t.split()[0] for t in texts] == [f"[{n}]" if style == "ieee" else f"{n}." for n in range(1, 9)]
        for text, expected in zip(texts, ref):
            # the same entry is in the same place: its first word agrees (a name or a number label)
            assert _norm_py("text", text).split()[0] == _norm_js("text", expected["out"]).split()[0]


def test_matches_citeproc_js_except_for_listed_differences():
    found = _differences()
    unexpected = {k: v for k, v in found.items() if k not in ALLOWED}
    assert not unexpected, "new differences from citeproc-js:\n" + "\n".join(f"{k}\n  py: {a}\n  js: {b}" for k, (a, b) in unexpected.items())
    gone = sorted(set(ALLOWED) - set(found))
    assert not gone, f"listed differences that no longer occur (remove them): {gone}"
    for key, (a, b) in found.items():
        normalize = [REASONS[r] for r in ALLOWED[key]]
        for f in normalize:
            a, b = f(a), f(b)
        assert a == b, f"{key}: the listed reasons {ALLOWED[key]} do not explain the difference\n  py: {a}\n  js: {b}"


def test_every_style_and_item_kind_is_covered():
    assert set(STYLES) == {"apa", "ieee", "chicago-author-date", "nature", "vancouver", "acm-sig-proceedings"}
    assert {i["type"] for i in ITEMS} >= {"article-journal", "paper-conference", "article", "book", "chapter", "thesis"}
    assert any("DOI" not in i for i in ITEMS) and any("DOI" in i for i in ITEMS)
    assert any("issued" not in i for i in ITEMS)
    assert {len(i["author"]) for i in ITEMS} >= {1, 2, 8}
    assert any(re.search(r"[぀-鿿]", i["author"][0]["family"]) for i in ITEMS)


@pytest.mark.parametrize("style", list(STYLES))
def test_text_has_no_markup(style):
    for entry in format_entries(ITEMS, style, "text", "en-US"):
        assert "<" not in entry and "&amp;" not in entry
