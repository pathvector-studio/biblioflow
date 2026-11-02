from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.errors import register_exception_handlers
from app.core.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.core.redis import close_redis
from app.db.session import dispose_engine


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await close_redis()
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="BiblioFlow API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.enable_docs else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.enable_docs else None,
    )
    # Added innermost first; CORS ends up outermost so even 413s carry CORS headers.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_upload_bytes)
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.cookie_secure)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )
    register_exception_handlers(app)
    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
