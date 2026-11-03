"""Author name helpers shared by the metadata clients."""

from __future__ import annotations

import re

from app.schemas.metadata import Author


def clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def split_name(name: str) -> Author | None:
    """``"Ada B. Lovelace"`` -> family ``Lovelace``, given ``Ada B.``."""
    parts = clean(name).split(" ")
    if not parts or not parts[0]:
        return None
    family = parts[-1][:200]
    given = " ".join(parts[:-1])[:200] or None
    return Author(family=family, given=given)
