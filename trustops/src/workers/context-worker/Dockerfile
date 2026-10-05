# H4 context-worker (design A §11.1). Build context is the repo root (the
# uv workspace spans src/, and fcrm-context-worker depends on sibling
# workspace packages such as fcrm-contracts and fcrm-seed):
#
#   docker build -f src/workers/context-worker/Dockerfile -t trustops/context-worker:local .
#
# Carries a clone of AEO's evoq_context engine (UPSTREAM.md) plus the thin
# fcrm_context_worker host package. `evoq-context` is a CLI entry point
# (evoq_context.cli:main) for the upstream engine, not a FastStream
# consumer — it does not serve HTTP. The `s3vectors` extra (aioboto3) is
# never installed here (VECTOR_BACKEND defaults to `memory`; it also
# conflicts with fcrm-agents in this workspace's lock — see this
# package's pyproject.toml and UPSTREAM.md).
#
# NOTE (as of this build): fcrm_context_worker has only its segmenter,
# filters and search pipeline — no fcrm_context_worker/__main__.py, no
# FastStream/fcrm-common dependency yet, so there is no process this
# image can run and no /readyz for the health port (9103) to answer. The
# image below builds and installs cleanly; `python -m fcrm_context_worker`
# will fail with "No module named fcrm_context_worker.__main__" at
# container start until H4's FastStream host layer lands.
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
COPY src/ ./src/
RUN cd src && uv sync --frozen --no-dev --no-editable --package fcrm-context-worker

FROM python:3.13-slim AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

USER app
EXPOSE 9103

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:9103/readyz', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["python", "-m", "fcrm_context_worker"]
