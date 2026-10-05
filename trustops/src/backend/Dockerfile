# syntax=docker/dockerfile:1.7
# Backend image (ADR-26): one image for both Python processes. `api` runs
# `uvicorn fcrm_api.main:app` and `harness` runs `python -m fcrm_harness`;
# docker-compose.yaml supplies the `command:`, so there is no ENTRYPOINT here
# and no HEALTHCHECK either (the two processes probe different ports).
#
# Build context is the repo root and the whole src/backend tree is copied:
# uv resolves the workspace from src/backend/pyproject.toml + uv.lock and
# needs every member's source on disk to build the ones the processes depend
# on as local wheels.
#
#   docker build -f src/backend/Dockerfile -t trustops/backend:local .

FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app
COPY src/backend/ ./src/
# Install only what the two processes import (the composites and, through
# them, their services, workers and packages). --all-packages is avoided on
# purpose: it would also build dev-only members such as fcrm-evals into the
# image. The cache mount keeps wheels between builds so a source-only change
# re-installs in seconds.
RUN --mount=type=cache,target=/root/.cache/uv \
    cd src && uv sync --frozen --no-dev --no-editable --package fcrm-api

FROM python:3.13-slim AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

USER app
EXPOSE 8000
