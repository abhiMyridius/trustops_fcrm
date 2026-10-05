# H3 ingestion-worker (design A §11.1). Build context is the repo root
# (the uv workspace spans src/, and fcrm-ingestion-worker depends on
# sibling workspace packages such as fcrm-contracts and fcrm-guardrails):
#
#   docker build -f src/workers/ingestion-worker/Dockerfile -t trustops/ingestion-worker:local .
#
# Carries a pruned clone of AEO's evoq_ingestion engine (UPSTREAM.md) plus
# the thin fcrm_ingestion_worker host package. `evoq-ingestion` is a CLI
# entry point (evoq_ingestion.cli:main) for the upstream engine, not a
# FastStream consumer — it does not serve HTTP.
#
# NOTE (as of this build): fcrm_ingestion_worker has only its document ->
# canonical-Markdown pipeline (.anchors, .guard1, .jobs.ingest_document) —
# no fcrm_ingestion_worker/__main__.py, no FastStream/fcrm-common
# dependency yet, so there is no process this image can run and no
# /readyz for the health port (9102) to answer. The image below builds
# and installs cleanly; `python -m fcrm_ingestion_worker` will fail with
# "No module named fcrm_ingestion_worker.__main__" at container start
# until H3's FastStream host layer lands.
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
COPY src/backend/ ./src/
RUN cd src && uv sync --frozen --no-dev --no-editable --package fcrm-ingestion-worker

FROM python:3.13-slim AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

USER app
EXPOSE 9102

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:9102/readyz', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["python", "-m", "fcrm_ingestion_worker"]
