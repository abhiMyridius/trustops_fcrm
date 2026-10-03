# S4 audit-service (design A §11.1). Build context is the repo root (the
# uv workspace spans src/, and fcrm-audit-service depends on sibling
# workspace packages such as fcrm-contracts):
#
#   docker build -f src/services/audit/Dockerfile -t trustops/audit:local .
#
# NOTE (as of this build): fcrm_audit has no fcrm_audit/main.py yet (only
# fcrm_audit.chain's pure, I/O-free hash-chain core has landed — no
# FastAPI/uvicorn dependency in pyproject.toml either). The image below
# builds and installs cleanly; `uvicorn fcrm_audit.main:app` will fail at
# container start until S4's HTTP layer lands, matching the pattern every
# other service in this repo already uses (`fcrm_scoring.main:app`).
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
RUN cd src && uv sync --frozen --no-dev --no-editable --package fcrm-audit-service

FROM python:3.13-slim AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

USER app
EXPOSE 8004

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:8004/api/fcrm/audit/readyz', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["uvicorn", "fcrm_audit.main:app", "--host", "0.0.0.0", "--port", "8004"]
