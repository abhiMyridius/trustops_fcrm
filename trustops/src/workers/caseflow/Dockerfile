# H1 Caseflow (design A §11.1). Build context is the repo root (the uv
# workspace spans src/, and fcrm-caseflow depends on sibling workspace
# packages such as fcrm-contracts):
#
#   docker build -f src/workers/caseflow/Dockerfile -t trustops/caseflow:local .
#
# NOTE (as of this build): fcrm_caseflow has only its pure, I/O-free state
# machine (.states, .transitions, .routing, .triggers, .eventstore) — no
# fcrm_caseflow/__main__.py, no FastStream/fcrm-common dependency yet, so
# there is no process this image can run and no /readyz for the health
# port (9100) to answer. The image below builds and installs cleanly;
# `python -m fcrm_caseflow` will fail with "No module named
# fcrm_caseflow.__main__" at container start until H1's FastStream host
# layer lands (design A §11.1's documented
# `AsgiFastStream(broker, asgi_routes=[...])` wiring, still a TODO in
# fcrm_common.health's own docstring as of this build).
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
RUN cd src && uv sync --frozen --no-dev --no-editable --package fcrm-caseflow

FROM python:3.13-slim AS runtime

RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:${PATH}"

USER app
EXPOSE 9100

HEALTHCHECK --interval=10s --timeout=5s --start-period=30s --retries=12 \
  CMD ["python", "-c", "import sys, urllib.request; sys.exit(0 if urllib.request.urlopen('http://localhost:9100/readyz', timeout=4).status == 200 else 1)"]

ENTRYPOINT ["python", "-m", "fcrm_caseflow"]
