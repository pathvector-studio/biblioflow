"""Formatted bibliographies from CSL JSON items (citeproc-py with bundled CSL styles and locales).

``format_entries`` is the pure formatter. Nothing here talks to the network: the styles and locales are files in
this directory (see ``README.md`` for where they come from and under which license). The API never calls
``format_entries`` in its own process; it goes through ``app.services.csl.isolated`` (a resource-limited child),
because the items are untrusted input (imported files, other workspace members).
"""

from __future__ import annotations

import html as html_lib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

DATA_DIR = Path(__file__).resolve().parent
STYLES_DIR = DATA_DIR / "styles"
LOCALES_DIR = DATA_DIR / "locales"

LOCALES = ("en-US", "ja-JP")
DEFAULT_LOCALE = "en-US"
Format = Literal["text", "html"]


class BibliographyError(Exception):
    """The items could not be formatted (message is safe to show to the user)."""


@dataclass(frozen=True)
class StyleInfo:
    id: str
    title: str
    kind: Literal["author-date", "numeric"]
    file: str


def _load_styles() -> dict[str, StyleInfo]:
    rows = json.loads((DATA_DIR / "styles.json").read_text(encoding="utf-8"))
    return {r["id"]: StyleInfo(**r) for r in rows}


STYLES: dict[str, StyleInfo] = _load_styles()

_CJK = re.compile(r"[⺀-鿿豈-﫿＀-￯　-〿\U00020000-\U0002ffff]")
_UNTOUCHED_KEYS = frozenset({"id", "type"})


def _is_cjk_name(name: dict[str, Any]) -> bool:
    family, given = name.get("family"), name.get("given")
    return (
        isinstance(family, str)
        and bool(_CJK.search(family))
        and (not given or (isinstance(given, str) and bool(_CJK.search(given))))
        and "literal" not in name
    )


def _prepare(value: Any, escape: bool, key: str = "") -> Any:
    """Copy of a CSL JSON value for citeproc-py.

    - HTML output: every string is HTML-escaped here. citeproc-py's HTML formatter does not escape field values
      (it passes ``<b>`` in a title through as markup), so the escaping is done before the processor sees them.
    - A name written in Chinese / Japanese characters becomes a literal name (family and given joined without
      a space). citeproc-py has no notion of such names and would otherwise print "山田, . 太郎 ." (citeproc-js
      prints "山田太郎").
    """
    if isinstance(value, str):
        return html_lib.escape(value, quote=False) if escape and key not in _UNTOUCHED_KEYS else value
    if isinstance(value, list):
        return [_prepare(v, escape, key) for v in value]
    if isinstance(value, dict):
        if _is_cjk_name(value):
            literal = f"{value['family']}{value.get('given') or ''}"
            return {"literal": html_lib.escape(literal, quote=False) if escape else literal}
        return {k: _prepare(v, escape, k) for k, v in value.items()}
    return value


def format_entries(items: list[dict[str, Any]], style: str, fmt: Format = "text", locale: str = DEFAULT_LOCALE) -> list[str]:
    """One formatted string per item, in bibliography order (sorted as the style says; numeric styles keep ``items`` order).

    In HTML each entry is an HTML fragment with every field value escaped; in text it is plain text.
    """
    if style not in STYLES:
        raise BibliographyError(f"Unknown style: {style}")
    if locale not in LOCALES:
        raise BibliographyError(f"Unknown locale: {locale}")
    if fmt not in ("text", "html"):
        raise BibliographyError(f"Unknown format: {fmt}")
    if not items:
        return []

    # citeproc-py is imported here so that importing this package (for the style list) stays cheap.
    import citeproc.frontend as frontend  # noqa: PLC0415
    from citeproc import Citation, CitationItem, CitationStylesBibliography, CitationStylesStyle  # noqa: PLC0415
    from citeproc.formatter import html as html_formatter  # noqa: PLC0415
    from citeproc.formatter import plain as plain_formatter  # noqa: PLC0415
    from citeproc.source.json import CiteProcJSON  # noqa: PLC0415

    _patch_citeproc()
    frontend.LOCALES_PATH = str(LOCALES_DIR)  # our pinned locale files, not the ones inside the package
    prepared = [_prepare(i, fmt == "html") for i in items]
    try:
        # validate=False: the style and locale files are ours (and RelaxNG validation of a 170 KB style is slow).
        csl_style = CitationStylesStyle(str(STYLES_DIR / STYLES[style].file), locale=locale, validate=False)
        bib = CitationStylesBibliography(csl_style, CiteProcJSON(prepared), html_formatter if fmt == "html" else plain_formatter)
        bib.register(Citation([CitationItem(i["id"]) for i in prepared]))
        bib.sort()
        return [_tidy(str(e), STYLES[style].kind, locale, fmt) for e in bib.bibliography()]
    except BibliographyError:
        raise
    except Exception as exc:  # citeproc-py raises assorted exceptions on malformed items
        raise BibliographyError("The bibliography could not be formatted") from exc


_NUMBER_LABEL = re.compile(r"^(\[\d+\]|\(\d+\)|\d+[.)])(?=\S)")
_PUNCTUATION_IN_QUOTE = {"en-US": True, "ja-JP": False}  # the `punctuation-in-quote` option of the bundled locales
_QUOTE_THEN_PUNCTUATION = re.compile(r"\u201d([.,])")


_DOT_AFTER_DOT_ITALIC = re.compile(r"\.</i>\.")


def _tidy(entry: str, kind: str, locale: str, fmt: str) -> str:
    """Fix-ups for what citeproc-py does differently from citeproc-js (the reference engine).

    - A citation number label is followed by a space: ``[1] J. A. Smith`` (citeproc-py glues the entry to the label).
    - In HTML, ``<i>et al.</i>.`` loses the second period (citeproc-py collapses doubled periods in plain text only).
    - With ``punctuation-in-quote`` (en-US) a comma or period after a closing double quote moves inside it:
      ``“Title,”`` (citeproc-py leaves it outside: ``“Title”,``).
    """
    entry = entry.strip()
    if kind == "numeric":
        entry = _NUMBER_LABEL.sub(r"\1 ", entry)
    if fmt == "html":
        entry = _DOT_AFTER_DOT_ITALIC.sub(".</i>", entry)
    if _PUNCTUATION_IN_QUOTE.get(locale):
        entry = _QUOTE_THEN_PUNCTUATION.sub(lambda m: m.group(1) + "\u201d", entry)
    return entry


_patched = False


def _patch_citeproc() -> None:
    """Honor ``initialize="false"`` on ``<name>``, which citeproc-py 0.12 ignores.

    Chicago (author-date) prints full given names (``Smith, John A.``) by setting ``initialize="false"`` on its
    names while the style root has ``initialize-with=". "``. Without this, citeproc-py reduces every given name to
    initials. The change is limited to that one option lookup, and ``tests/test_csl_reference.py`` fails if the
    behavior of the pinned version changes.
    """
    global _patched
    if _patched:
        return
    from citeproc.model import Name  # noqa: PLC0415

    original = Name.get_option

    def get_option(self, name, context=None, sort_options=None):  # type: ignore[no-untyped-def]
        if name == "initialize-with" and sort_options is None and self.get("initialize") == "false":
            return None
        return original(self, name, context, sort_options)

    Name.get_option = get_option  # type: ignore[method-assign]
    _patched = True
