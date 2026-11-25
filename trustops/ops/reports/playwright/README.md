# End-to-end test results, 25 November 2026 (ADR-90)

Run on a local stack (`make up-debug`) built from `main` at 30856c9. The host ports were moved off
the ones other projects on the machine hold (gateway `18080`, Mongo `27117`, RabbitMQ `15680`,
OPA `18181`, Redis `16379`, OTLP `14318`); the suites read `E2E_BASE_URL=http://localhost:18080`.

| Suite | Result | Real AI calls | Report |
|---|---|---|---|
| Mock-backend scenes (`npm run test:e2e`, 8 tests) | **8 passed**, 34 s | none | `mock-scenes-html/index.html` |
| Core flow, case FCRM-2026-0151 (`make demo`, 21 scenes) | **11 passed, 1 failed (scene 12), 9 skipped**, 2 min 35 s | yes | `demo-0151-pytest.log` |
| Live browser scenes (`npm run test:e2e:live`, 12 tests) | **10 passed, 2 failed**, 43 s | none | `live-scenes-html/index.html` |
| Python unit tests with coverage (`make coverage`) | 8,883 passed, 266 skipped; coverage 91% (lines 92.9%, branches 83.4%) | none | `python-coverage.log` |
| Web unit tests (`npx vitest run`) | 344 passed | none | not kept (console only) |

Not run, ready to run (they need the stack; they make no agent calls): the browser sweep,
81 tests, `cd src/frontend && E2E_BASE_URL=... npm run test:e2e:sweep` (about nine minutes,
writes `src/frontend/test-results/sweep-report.json`).

## What failed

1. **Core flow stops at scene 12.** The analyst's first clause decision answers 409 `wrong_stage`
   ("items are decided once review has started (the case is at clarification)"). Scenes 1 to 11
   pass, including the owner's answers in scene 11, but the case stays at "awaiting your answer"
   and never moves to review. The harness is idle from 09:58:43, logs no error and every
   dead-letter queue is empty, so the engine is waiting, not crashed. **Cause not determined.**
   It is not known whether this predates the SonarQube remediation commits: `tests/e2e/test_demo_0151.py`
   was last changed on 19 October, and known-gaps already records that no complete live pass exists
   for the committed model chain. A run of the commit before the remediation would settle it
   (it calls Bedrock, so it needs the operator's approval, ADR-59).
2. **Live scenes, 2 failures:** "the demo case is in the queue once the demo has closed it"
   (the demo did not close it) and "an open pack shows its votes as hidden until all three are cast"
   (element not found on the committee page; not investigated).

## Cost

`make token-report` (`ops/token-analysis/runtime/2026-11-25.md`): 29,088 tokens, about USD 0.10 for
the agents, plus about USD 0.01 for embedding the 197 policy clauses at start-up. Approved ceiling
was about USD 1.

## Setup notes

- The MongoDB volume dated from 19 October, so its demo users kept an older password hash and the
  demo login answered 401. `make seed` (approved) reloaded the seed and fixed it.
- Playwright's Chromium (build 1243) had to be installed: `npx playwright install chromium`.
- Stop the stack with `make down` (the data stays); `make down-v` also deletes it.
