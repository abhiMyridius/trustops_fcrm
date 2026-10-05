# S1 case-service (design A §11.1). Build context is the repo root (the uv
# workspace spans src/, and fcrm-case-service depends on sibling workspace
# packages such as fcrm-contracts):
#
#   docker build -f src/services/case/Dockerfile -t trustops/case:local .
#
# `uvicorn fcrm_case.main:app` serves fcrm_case.routes under APP_ROOT_PATH
# (/api/fcrm in compose, so the healthcheck below hits /api/fcrm/readyz); it
# needs MONGODB_URI (a replica set: create/submit write the case and its
# outbox row in one transaction), SERVICE_TOKEN_SECRET, OPA_URL and one of
# IDENTITY_JWKS_URL / IDENTITY_VALIDATE_URL, and exits at startup naming every
# missing variable (fcrm_common.settings.MissingSettingsError) rather than
# serving traffic half-configured.
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
RUN cd src && uv sync --frozen --no-dev --no-editable --package fcrm-case-service

FROM python:3.13-slim AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

USER app
EXPOSE 8001

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:8001/api/fcrm/readyz', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["uvicorn", "fcrm_case.main:app", "--host", "0.0.0.0", "--port", "8001"]
