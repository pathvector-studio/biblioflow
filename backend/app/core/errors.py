import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger("biblioflow")


class RateLimitExceeded(HTTPException):
    def __init__(self, retry_after: int) -> None:
        super().__init__(
            status_code=429,
            detail="Too many requests",
            headers={"Retry-After": str(max(1, retry_after))},
        )


def not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Not found")


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse({"detail": "Internal server error"}, status_code=500)
