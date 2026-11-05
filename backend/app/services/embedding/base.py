from __future__ import annotations

from typing import Literal, Protocol

Kind = Literal["document", "query"]
MAX_TEXT_CHARS = 8000  # per text; the embedder container enforces the same limit
DIMENSION = 1024


class EmbeddingError(Exception):
    """The provider is unreachable, too slow, or answered something unusable. The message is safe to log."""


class Embedder(Protocol):
    model_id: str

    async def embed(self, texts: list[str], kind: Kind, timeout: float | None = None) -> list[list[float]]: ...


def clip(texts: list[str]) -> list[str]:
    return [t[:MAX_TEXT_CHARS] for t in texts]


def check_vectors(vectors: object, expected: int) -> list[list[float]]:
    """Validate a provider's answer: right count, 1024 floats each."""
    if not isinstance(vectors, list) or len(vectors) != expected:
        raise EmbeddingError("embedding provider returned an unexpected number of vectors")
    for v in vectors:
        if not isinstance(v, list) or len(v) != DIMENSION:
            raise EmbeddingError(f"embedding provider returned vectors of the wrong size (need {DIMENSION})")
    return vectors
