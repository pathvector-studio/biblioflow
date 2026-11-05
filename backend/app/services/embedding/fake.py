"""Deterministic embedder for tests: a hashed bag of words, so texts that share words are close."""

from __future__ import annotations

import hashlib
import math
import re

from app.services.embedding.base import DIMENSION, Kind

_WORD = re.compile(r"\w+")


def fake_vector(text: str) -> list[float]:
    vec = [0.0] * DIMENSION
    for word in _WORD.findall(text.lower()):
        vec[int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big") % DIMENSION] += 1.0
    norm = math.sqrt(sum(x * x for x in vec))
    if norm == 0:
        vec[0], norm = 1.0, 1.0
    return [x / norm for x in vec]


class FakeEmbedder:
    def __init__(self, model_id: str = "fake@test") -> None:
        self.model_id = model_id
        self.calls: list[tuple[Kind, int]] = []

    async def embed(self, texts: list[str], kind: Kind, timeout: float | None = None) -> list[list[float]]:
        self.calls.append((kind, len(texts)))
        return [fake_vector(t) for t in texts]
