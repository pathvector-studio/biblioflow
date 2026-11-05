"""HTTP embedding providers. The destinations are set by the administrator (``.env``), not by users, so they do
not go through ``safe_client`` (the SSRF guard for user-supplied URLs); an Ollama / vLLM on the LAN must work."""

from __future__ import annotations

import logging

import httpx

from app.embedder import spec
from app.services.embedding.base import EmbeddingError, Kind, check_vectors, clip

logger = logging.getLogger("biblioflow.embedding")
DOCUMENT_TIMEOUT = 120.0
VOYAGE_URL = "https://api.voyageai.com/v1"
OPENAI_URL = "https://api.openai.com/v1"


async def _post(
    url: str,
    *,
    json: object,
    headers: dict[str, str],
    timeout: float,
    what: str,
    transport: httpx.AsyncBaseTransport | None = None,
) -> object:
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, transport=transport) as client:
            response = await client.post(url, json=json, headers=headers)
    except httpx.TimeoutException:
        raise EmbeddingError(f"{what}: timed out") from None
    except httpx.HTTPError as exc:
        raise EmbeddingError(f"{what}: {type(exc).__name__}") from None
    if response.status_code != 200:
        # The body of an error can echo the request (the text of a paper), so it is not logged or raised.
        raise EmbeddingError(f"{what}: HTTP {response.status_code}")
    try:
        return response.json()
    except ValueError:
        raise EmbeddingError(f"{what}: answer is not JSON") from None


class LocalEmbedder:
    """The ``biblioflow-embedder`` container (bge-m3, ONNX Runtime) on the internal network."""

    def __init__(self, url: str, precision: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport
        self.url = url.rstrip("/")
        self.model_id = spec.model_id(precision)

    async def embed(self, texts: list[str], kind: Kind, timeout: float | None = None) -> list[list[float]]:
        body = await _post(
            f"{self.url}/embed",
            json={"texts": clip(texts), "kind": kind},
            headers={},
            timeout=timeout or DOCUMENT_TIMEOUT,
            what="embedder",
            transport=self.transport,
        )
        if not isinstance(body, dict):
                    raise EmbeddingError("embedder: unexpected answer")
        return check_vectors(body.get("vectors"), len(texts))


def _bearer(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"} if key else {}


def _sorted_data(body: object, what: str) -> list[list[float]]:
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise EmbeddingError(f"{what}: unexpected answer")
    try:
        return [item["embedding"] for item in sorted(data, key=lambda d: d.get("index", 0))]
    except (KeyError, TypeError, AttributeError):
        raise EmbeddingError(f"{what}: unexpected answer") from None


class VoyageEmbedder:
    def __init__(
        self, api_key: str, model: str, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.transport = transport
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or VOYAGE_URL).rstrip("/")
        self.model_id = model

    async def embed(self, texts: list[str], kind: Kind, timeout: float | None = None) -> list[list[float]]:
        body = await _post(
            f"{self.base_url}/embeddings",
            json={"input": clip(texts), "model": self.model, "input_type": kind, "output_dimension": 1024},
            headers=_bearer(self.api_key),
            timeout=timeout or DOCUMENT_TIMEOUT,
            what="voyage",
            transport=self.transport,
        )
        return check_vectors(_sorted_data(body, "voyage"), len(texts))


class OpenAIEmbedder:
    """OpenAI, or any OpenAI-compatible server through ``EMBEDDING_BASE_URL`` (Ollama, vLLM, TEI, ...)."""

    def __init__(
        self, api_key: str, model: str, base_url: str | None = None, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.transport = transport
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or OPENAI_URL).rstrip("/")
        self.model_id = f"openai:{model}"

    async def embed(self, texts: list[str], kind: Kind, timeout: float | None = None) -> list[list[float]]:
        payload: dict[str, object] = {"input": clip(texts), "model": self.model}
        if self.model.startswith("text-embedding-3"):
            payload["dimensions"] = 1024  # other servers return the model's own size and are checked below
        body = await _post(
            f"{self.base_url}/embeddings",
            json=payload,
            headers=_bearer(self.api_key),
            timeout=timeout or DOCUMENT_TIMEOUT,
            what="openai",
            transport=self.transport,
        )
        return check_vectors(_sorted_data(body, "openai"), len(texts))
