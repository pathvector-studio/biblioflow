import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.v1.papers import ingest_error_handler
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.queue import close_arq_pool
from app.core.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.core.redis import close_redis
from app.db.session import dispose_engine
from app.services.ingest import IngestError

# Every route except the PDF upload takes JSON; notes (<= 500k chars) fit in 4 MiB.
JSON_BODY_LIMIT = 4 * 1024 * 1024
IMPORT_BODY_LIMIT = 5 * 1024 * 1024  # 4 MiB file + multipart envelope; same value in deploy/Caddyfile
UPLOAD_PATH = "/api/v1/papers/upload"
IMPORT_PATHS = ("/api/v1/imports", "/api/v1/imports/preview")  # bibliography file, see api/v1/imports.py


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    problem = get_settings().llm_config_problem
    if problem:  # chat stays off (503 llm_disabled) instead of the API refusing to start
        logging.getLogger("biblioflow.llm").warning("AI chat is disabled: %s (set LLM_PROVIDER=none to silence)", problem)
    yield
    await close_arq_pool()
    await close_redis()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="BiblioFlow API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.enable_docs else None,
    )
    # Added innermost first; CORS ends up outermost so even 413s carry CORS headers.
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=min(JSON_BODY_LIMIT, settings.max_upload_bytes),
        path_limits={UPLOAD_PATH: settings.max_upload_bytes, **dict.fromkeys(IMPORT_PATHS, IMPORT_BODY_LIMIT)},
    )
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )
    register_exception_handlers(app)
    app.add_exception_handler(IngestError, ingest_error_handler)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
