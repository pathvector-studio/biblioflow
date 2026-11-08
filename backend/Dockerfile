# syntax=docker/dockerfile:1
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.3 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
ARG INSTALL_DEV=false
COPY pyproject.toml uv.lock ./
RUN if [ "$INSTALL_DEV" = "true" ]; then \
      uv sync --frozen; \
    else \
      uv sync --frozen --no-dev; \
    fi

FROM python:3.12-slim AS runtime
ARG UID=1000
ARG GID=1000
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"
RUN groupadd --gid "${GID}" app \
 && useradd --uid "${UID}" --gid "${GID}" --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin app \
 && mkdir -p /data/library /data/watch /data/exports /models \
 && chown -R app:app /data /models
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY app ./app
# Pre-compiled: the root filesystem is read-only, and the PDF parser child process imports app on every upload.
RUN python -m compileall -q app
COPY tests ./tests
USER app
EXPOSE 8000
# --forwarded-allow-ips=* is safe only because this container is never published:
# the API is reachable solely through biblioflow-gateway (Caddy), which overwrites
# X-Forwarded-For / X-Forwarded-Proto on every request. Do not add `ports:` to the API.
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=*"]
