"""``biblioflow-embedder``: one resident model behind a tiny HTTP API on the internal network.

    GET  /healthz
    POST /embed   {"texts": [...], "kind": "document"|"query"}  ->  {"model": "...", "vectors": [[...], ...]}

No authentication: the container is reachable only from ``biblioflow-internal`` and holds no data or secrets.
It must never import ``app.core.config``.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field, StringConstraints
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.embedder import spec

logger = logging.getLogger("biblioflow.embedder")

MAX_TEXTS = 32
MAX_CHARS = 8000
MAX_BODY_BYTES = 2 * 1024 * 1024  # 32 x 8000 characters, even if every one is escaped to \uXXXX in UTF-8 JSON, fits


class EmbedRequest(BaseModel):
    texts: list[Annotated[str, StringConstraints(max_length=MAX_CHARS)]] = Field(min_length=1, max_length=MAX_TEXTS)
    kind: Literal["document", "query"] = "document"


class BodyLimit:
    """Reject bodies over the limit by Content-Length, and count bytes when the length is not declared."""

    def __init__(self, app: ASGIApp, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        too_big = False
        try:
            too_big = declared is not None and int(declared) > self.limit
        except ValueError:
            too_big = True
        if too_big:
            await self._reject(scope, receive, send)
            return
        received = 0

        async def limited() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise HTTPException(413, "request body too large")  # FastAPI passes HTTPException through
            return message

        await self.app(scope, limited, send)

    async def _reject(self, scope: Scope, receive: Receive, send: Send) -> None:
        body = b'{"detail":"request body too large"}'
        await send(
            {
                "type": "http.response.start",
                "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())],
            }
        )
        await send({"type": "http.response.body", "body": body})


def create_app(engine: Any | None = None, model_name: str | None = None) -> FastAPI:
    """``engine`` is anything with ``encode(list[str]) -> ndarray``; by default the ONNX model is loaded at startup
    when ``EMBEDDING_PROVIDER=local``. With any other provider no model is loaded and ``/embed`` answers 503."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if engine is not None:
            app.state.engine, app.state.model = engine, model_name or "test"
        elif spec.env_provider() == "local":
            from app.embedder.model import OnnxEmbedder, threads_from_env

            precision = spec.env_precision()
            models_dir = spec.env_models_dir()
            path = spec.model_file(models_dir, precision)
            logger.info("loading %s", path)
            app.state.engine = OnnxEmbedder(path, spec.model_dir(models_dir) / "tokenizer.json", threads_from_env())
            app.state.model = spec.model_id(precision)
            logger.info("model loaded")
        else:
            app.state.engine, app.state.model = None, None
            logger.info("EMBEDDING_PROVIDER=%s: no local model is loaded", spec.env_provider())
        yield

    app = FastAPI(title="biblioflow-embedder", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(BodyLimit, limit=MAX_BODY_BYTES)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "model": app.state.model}

    @app.post("/embed")
    async def embed(body: EmbedRequest, request: Request) -> dict[str, Any]:
        eng = request.app.state.engine
        if eng is None:
            raise HTTPException(503, "no local model: EMBEDDING_PROVIDER is not 'local'")
        # In a thread, so /healthz keeps answering during a long request; the engine serialises forward passes.
        vectors = await asyncio.to_thread(eng.encode, body.texts)
        return {"model": request.app.state.model, "vectors": vectors.tolist()}

    return app


app = create_app()
