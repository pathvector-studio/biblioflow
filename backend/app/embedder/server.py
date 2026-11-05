"""``biblioflow-embedder``: one resident model behind a tiny HTTP API on the internal network.

    GET  /healthz   ->  {"status": "ok", "model": "disabled|absent|loading|ready", "model_id": ...}
    POST /embed   {"texts": [...], "kind": "document"|"query"}  ->  {"model": "...", "vectors": [[...], ...]}

No authentication: the container is reachable only from ``biblioflow-internal`` and holds no data or secrets.
It must never import ``app.core.config``.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable
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


def poll_seconds() -> float:
    try:
        return max(0.05, float(os.environ.get("EMBEDDER_POLL_SECONDS", "10")))
    except ValueError:
        return 10.0


class ModelState:
    """What ``/healthz`` reports as ``model``: disabled (not the local provider), absent (no READY marker yet),
    loading (verifying and loading the files), ready."""

    DISABLED, ABSENT, LOADING, READY = "disabled", "absent", "loading", "ready"


def load_local_model(on_loading: Callable[[], None] = lambda: None) -> tuple[Any, str] | None:
    """Load the model if model-init has finished (READY holds this model's id) and the files still match their
    pinned sha256. The check costs 1-2 s for the int8 model: the volume is shared and can be restored or edited
    from outside, so a marker alone is not trusted. Returns ``None`` when it is not there (yet)."""
    from app.embedder import fetch
    from app.embedder.model import OnnxEmbedder, threads_from_env

    precision = spec.env_precision()
    models_dir = spec.env_models_dir()
    wanted = spec.model_id(precision)
    if fetch.read_ready(models_dir) != wanted:
        return None
    on_loading()
    if not fetch.files_valid(models_dir, precision):
        logger.error("READY is present but the model files do not match their checksums: not loading them")
        return None
    path = spec.model_file(models_dir, precision)
    logger.info("loading %s", path)
    engine = OnnxEmbedder(path, spec.model_dir(models_dir) / "tokenizer.json", threads_from_env())
    logger.info("model loaded")
    return engine, wanted


async def watch_for_model(app: FastAPI, loader: Callable[[Callable[[], None]], tuple[Any, str] | None]) -> None:
    """Poll until the model can be loaded; the server answers (``/healthz``) the whole time."""
    interval = poll_seconds()
    while True:
        try:
            loaded = await asyncio.to_thread(loader, lambda: setattr(app.state, "model_state", ModelState.LOADING))
        except Exception:  # noqa: BLE001 - a bad file must not kill the server; try again later
            logger.exception("loading the model failed")
            loaded = None
        if loaded is not None:
            app.state.engine, app.state.model = loaded
            app.state.model_state = ModelState.READY
            return
        app.state.model_state = ModelState.ABSENT
        await asyncio.sleep(interval)


def create_app(
    engine: Any | None = None,
    model_name: str | None = None,
    loader: Callable[[Callable[[], None]], tuple[Any, str] | None] | None = None,
) -> FastAPI:
    """``engine`` is anything with ``encode(list[str]) -> ndarray``. Otherwise, with ``EMBEDDING_PROVIDER=local``,
    a background task waits for model-init's READY marker and loads the model (``loader`` is for tests). With any
    other provider nothing is loaded. Either way the process starts at once and is healthy; ``/embed`` answers 503
    until a model is loaded."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine, app.state.model, app.state.model_state = None, None, ModelState.DISABLED
        task = None
        if engine is not None:
            app.state.engine, app.state.model = engine, model_name or "test"
            app.state.model_state = ModelState.READY
        elif loader is not None or spec.env_provider() == "local":
            app.state.model_state = ModelState.ABSENT
            task = asyncio.create_task(watch_for_model(app, loader or load_local_model))
        else:
            logger.info("EMBEDDING_PROVIDER=%s: no local model is loaded", spec.env_provider())
        try:
            yield
        finally:
            if task is not None:
                task.cancel()

    app = FastAPI(title="biblioflow-embedder", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.add_middleware(BodyLimit, limit=MAX_BODY_BYTES)

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "model": app.state.model_state, "model_id": app.state.model}

    @app.post("/embed")
    async def embed(body: EmbedRequest, request: Request) -> dict[str, Any]:
        eng = request.app.state.engine
        if eng is None:
            raise HTTPException(503, "model not ready")
        # In a thread, so /healthz keeps answering during a long request; the engine serialises forward passes.
        vectors = await asyncio.to_thread(eng.encode, body.texts)
        return {"model": request.app.state.model, "vectors": vectors.tolist()}

    return app


app = create_app()
