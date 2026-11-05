"""Delimit text that originates from a paper so the model treats it as data, not instructions."""

from __future__ import annotations

import re

_CLOSING = re.compile(r"<\s*/\s*paper_context", re.IGNORECASE)
_OPENING = re.compile(r"<\s*paper_context", re.IGNORECASE)
# Attribute values keep any script (Japanese section titles) but nothing that could end the tag or the value.
_ATTR_BAD = re.compile(r"[\"'<>&\\=\x00-\x1f\x7f]")


def neutralize(text: str) -> str:
    """Make any ``paper_context`` tag inside ``text`` inert (it cannot close or fake our delimiter)."""
    text = _CLOSING.sub(lambda _: "<\\/paper_context", text)
    return _OPENING.sub(lambda _: "<\\paper_context", text)


def wrap(text: str, paper_id: str, **attrs: str | int | None) -> str:
    """``<paper_context paper_id=".." page="..">text</paper_context>`` with a neutralized body."""
    parts = [f'paper_id="{_ATTR_BAD.sub("", str(paper_id))}"']
    for key, value in attrs.items():
        if value is None:
            continue
        parts.append(f'{key}="{_ATTR_BAD.sub("", str(value))[:100]}"')
    return f"<paper_context {' '.join(parts)}>\n{neutralize(text)}\n</paper_context>"


def one_line(text: str, limit: int = 300) -> str:
    """Collapse whitespace (used for titles and other short strings shown outside the delimiter)."""
    flat = " ".join(neutralize(text).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"
