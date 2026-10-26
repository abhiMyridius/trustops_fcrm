# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Layout

This is the git root (`trustops_fcrm/`). It holds three siblings for one hackathon project (Genius Hacks Q3 2026, the FCRM Risk Assessment Workbench):

| Folder | What it is | Status |
|---|---|---|
| `trustops/` | The real system: FastAPI services, FastStream workers, Pydantic AI agents on Bedrock, MongoDB, RabbitMQ, React 19 frontend, eight containers | The product. Has its own [trustops/CLAUDE.md](trustops/CLAUDE.md), which is detailed and authoritative: read it before touching anything there |
| `FCRM_v2/` | The BA's clickable prototype: Vite + React 19 + Tailwind 4 with mock data only, no backend | Reference only. `trustops/` never imports or builds on it |
| `Documents/` | The approved spec set (HTML + `00-README.md` index), the brief, the kickoff transcript | Read-only record; a copy lives under `trustops/docs/requirements/source/` |

Nested `CLAUDE.md` files take precedence in their folder: `trustops/CLAUDE.md`, `trustops/ai/CLAUDE.md` (prompts, model tiers, guards: never edit a committed prompt, model ids only in `ai/models/tiers.yaml`) and `trustops/src/frontend/CLAUDE.md`. Do not duplicate their content here.

Where things are decided when sources disagree: the brief, then `trustops/docs/governance/decision-log.md` (ADR rows), then the rest of `trustops/` docs, then `Documents/`, then the `FCRM_v2` prototype (see "Precedence" in `trustops/CLAUDE.md`). `Documents/00-README.md` summarises the as-built decisions, the nine agents and the product rules.

## Working in `trustops/`

Run `make` only from `trustops/` (not from this root); `git` works from either level. Python goes through the uv workspace at `src/backend`, so use `uv run --project src/backend --all-packages ...`. The commit gate is `make check`; the full command list, the single-test recipe and the rules (outbox, PO fence, contracts-first, no Opus, generated files never hand-edited) are in `trustops/CLAUDE.md`.

Anything that calls Bedrock (`make evals`, `make demo`, `make e2e`) costs money and needs the operator's approval first (ADR-59). Never read `trustops/.env*`; never commit `.env` or `jwt_keys/`.

## Working in `FCRM_v2/` (prototype)

```bash
npm install
npm run dev      # vite dev server on :5173 (also .claude/launch.json "fcrm-dev")
npm run build    # tsc -b && vite build; the only type check
npm run lint     # oxlint (.oxlintrc.json)
```

There is no test runner. Path alias `@` maps to `src/`.

Architecture, which spans several files:

- **No backend.** `src/services/api.ts` (cases, intake, templates, integrations, dashboard) and `src/services/analystApi.ts` (analyst and committee side, about 3,000 lines) expose async functions that read and mutate an in-memory `store` (`src/services/store.ts`) seeded from `src/mocks/seed/*`. Pages call these, never the store directly.
- **Three personas**, switched through `PersonaContext`, each with its own route tree in `src/App.tsx`: product owner (`/`, `/queue`, `/case/:id`, `/case/:id/intake`, templates, integrations, AI configuration), analyst (`/analyst/*`: dashboard, queue, case workbench, assurance, config) and committee (`/committee/*`). All sit under one `AppLayout`.
- `src/services/scoring.ts` is a client-side stand-in for the scoring engine. In `trustops/` scoring lives only in the deterministic `fcrm_scoring` service, so do not port prototype arithmetic into the product.
- Types are in `src/types/index.ts` (owner side) and `src/types/analyst.ts` (analyst side).
