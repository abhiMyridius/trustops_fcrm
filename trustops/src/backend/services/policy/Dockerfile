# S3 policy-service (design A §11.1). Build context is the repo root (the
# uv workspace spans src/, and fcrm-policy-service depends on sibling
# workspace packages such as fcrm-contracts):
#
#   docker build -f src/services/policy/Dockerfile -t trustops/policy:local .
#
# fcrm_policy.main:app (the S3 HTTP layer) is mounted under APP_ROOT_PATH via
# fcrm_common.asgi.mount_at_root_path, as every service here does (D-13). The
# image carries fcrm-context-worker (H4's engine) as a library dependency, base
# install only: the aioboto3 `s3vectors` extra is not installed, so the image
# serves VECTOR_BACKEND=memory (the compose default); see
# src/services/policy/fcrm_policy/search/vector.py for why and what follows.
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
RUN cd src && uv sync --frozen --no-dev --no-editable --package fcrm-policy-service

FROM python:3.13-slim AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

USER app
EXPOSE 8003

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:8003/api/fcrm/policy/readyz', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["uvicorn", "fcrm_policy.main:app", "--host", "0.0.0.0", "--port", "8003"]
