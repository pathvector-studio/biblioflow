"""Embedding providers for semantic search: ``local`` (biblioflow-embedder), ``voyage``, ``openai``, ``none``.

``get_embedder()`` returns ``None`` when embeddings are switched off. Every vector is 1024-dimensional
(``vector(1024)`` in the schema) and L2-normalised by the provider or by us. ``Embedder.model_id`` is stored in
``paper_chunks.embedding_model`` so vectors from different models are never compared with each other.
"""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.services.embedding.base import MAX_TEXT_CHARS, Embedder, EmbeddingError, Kind, ModelNotReady
from app.services.embedding.fake import FakeEmbedder
from app.services.embedding.providers import LocalEmbedder, OpenAIEmbedder, VoyageEmbedder

__all__ = [
    "MAX_TEXT_CHARS",
    "Embedder",
    "EmbeddingError",
    "FakeEmbedder",
    "Kind",
    "ModelNotReady",
    "build_embedder",
    "current_model_id",
    "get_embedder",
    "model_state",
]


def build_embedder(settings: Settings) -> Embedder | None:
    provider = settings.embedding_provider
    if provider == "none":
        return None
    if provider == "local":
        return LocalEmbedder(settings.embedder_url, settings.embedding_local_precision)
    key = settings.embedding_api_key.get_secret_value() if settings.embedding_api_key else ""
    if provider == "voyage":
        return VoyageEmbedder(key, settings.embedding_model or "voyage-3.5", settings.embedding_base_url)
    return OpenAIEmbedder(
        key, settings.embedding_model or "text-embedding-3-large", settings.embedding_base_url
    )


def get_embedder() -> Embedder | None:
    """The configured provider, or ``None`` for ``EMBEDDING_PROVIDER=none``. Call it through the module
    (``embedding.get_embedder()``) so tests can replace it."""
    return build_embedder(get_settings())


def current_model_id() -> str | None:
    embedder = get_embedder()
    return embedder.model_id if embedder else None


async def model_state(embedder: Embedder | None) -> str:
    """``disabled`` (no provider), ``absent`` / ``loading`` / ``ready`` / ``unreachable`` for the local embedder
    (asked on its ``/healthz``, short timeout). An external provider (voyage, openai) is assumed ready: asking
    it would cost a billable call."""
    if embedder is None:
        return "disabled"
    if isinstance(embedder, LocalEmbedder):
        return await embedder.state()
    return "ready"
