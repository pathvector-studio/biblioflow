"""Small SQL helpers shared across services."""

from __future__ import annotations

LIKE_ESCAPE = "\\"


def escape_like(s: str) -> str:
    """Escape ``\\``, ``%`` and ``_`` so ``s`` matches literally inside a LIKE/ILIKE pattern (use ``escape=LIKE_ESCAPE``)."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
