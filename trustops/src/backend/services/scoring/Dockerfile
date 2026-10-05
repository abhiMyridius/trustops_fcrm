# S2 scoring-service (design A §11.1). Build context is the repo root
# (the uv workspace spans src/, and fcrm-scoring-service depends on the
# sibling workspace packages fcrm-contracts, fcrm-seed, fcrm-common):
#
#   docker build -f src/services/scoring/Dockerfile -t trustops/scoring:local .
#
# Multi-stage: `builder` resolves and installs the workspace with uv into a
# venv that is `--no-editable` (built wheels, not path installs), so the
# `runtime` stage only needs that venv copied in, never the source tree.
# `runtime` is plain python:3.13-slim with no uv, no build tools, non-root.

# syntax=docker/dockerfile:1.7

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
# The whole src/ tree: uv resolves the workspace from src/pyproject.toml +
# src/uv.lock and needs every workspace member's source on disk to build
# the ones fcrm-scoring-service depends on as local wheels.
COPY src/backend/ ./src/
RUN cd src && uv sync --frozen --no-dev --no-editable --package fcrm-scoring-service

FROM python:3.13-slim AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

USER app
EXPOSE 8002

# Mirrors docker-compose.yaml's own healthcheck test (stdlib only, no fcrm
# package import needed) so `docker run` alone still self-reports health.
HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:8002/api/fcrm/scoring/readyz', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["uvicorn", "fcrm_scoring.main:app", "--host", "0.0.0.0", "--port", "8002"]
