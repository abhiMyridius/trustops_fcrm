# W1 web (design A §11.1, C-web). Build context is the repo root — not
# just src/web — because src/web/tsconfig.app.json's `include` and
# src/web/src/mocks/seed.ts both reach across to the sibling workspace
# package `../../../packages/seed/fcrm_seed/seed.json` (the P7 seed,
# imported at build time, never copied or hand-edited), so `tsc -b` and
# `vite build` need that file present relative to src/web exactly as it
# sits in the real tree:
#
#   docker build -f src/web/Dockerfile -t trustops/web:local .
#
# Two stages: `builder` (node, matches package.json's engines.node >=22)
# runs the same `npm run build` the package's own CLAUDE.md documents
# (tsc -b && vite build -> src/web/dist); `runtime` is nginx serving that
# static build on :80, which docker-compose.yaml's gateway (G1) reverse
# proxies to. compose.dev.yaml swaps this whole container for the Vite
# dev server instead.

# syntax=docker/dockerfile:1.7

FROM node:22-slim AS builder

WORKDIR /app
COPY src/ ./src/
WORKDIR /app/src/web
# package-lock.json is committed (npm ci, not npm install: reproducible).
RUN npm ci
# Production default: talk to the real gateway-routed backend, not the
# in-browser MSW mock (VITE_API_MODE=msw is the dev-server default).
ENV VITE_API_MODE=live
RUN npm run build

FROM nginx:1.27-alpine AS runtime

COPY src/web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/src/web/dist /usr/share/nginx/html

# The stock nginx image already runs its master process as root and
# workers as nginx (uid 101); nginx needs root to bind :80 and manage its
# worker processes, so we keep the image's own user model rather than
# forcing a non-privileged one that cannot bind the port.
EXPOSE 80

HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=12 \
  CMD ["wget", "-q", "-O", "/dev/null", "http://localhost:80/"]
