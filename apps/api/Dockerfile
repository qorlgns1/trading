# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:0.8.22 AS uv
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock ./
COPY packages/quant-core/pyproject.toml packages/quant-core/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
RUN uv sync --locked --no-dev --package quant-api --no-install-workspace

COPY packages/quant-core packages/quant-core
COPY apps/api apps/api
RUN uv sync --locked --no-dev --package quant-api

RUN useradd --create-home --uid 10001 app && mkdir -p /app/artifacts /app/data \
    && chown -R app:app /app/artifacts /app/data
USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health/live', timeout=3)"
CMD ["uvicorn", "quant_api.main:app", "--app-dir", "apps/api/src", "--host", "0.0.0.0", "--port", "8000", "--workers", "2"]
