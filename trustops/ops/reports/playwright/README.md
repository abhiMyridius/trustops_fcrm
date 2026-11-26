# End-to-end test results, 25 November 2026 (ADR-90)

**Everything passes.** What each test covers and every test case with its steps: [`TEST-COVERAGE-AND-CASES.md`](TEST-COVERAGE-AND-CASES.md). Run on a local stack (`make up-debug`) built from `main`, with the
host ports moved off the ones other projects on the machine hold (gateway `18080`, Mongo
`27117`, RabbitMQ `15680`, OPA `18181`, Redis `16379`, OTLP `14318`; the suites read
`E2E_BASE_URL=http://localhost:18080`).

| Suite | Result | Real AI calls | Where |
|---|---|---|---|
| Mock-backend browser scenes (`npm run test:e2e`) | **8 of 8** | none | `mock-scenes-html/index.html` |
| Browser sweep, every screen as every role (`npm run test:e2e:sweep`) | **43 of 43** (582 checks passed, 0 failed, 107 notes, 32 not exercised) | none | `sweep/sweep-report.json`, `sweep/screens/` |
| Core flow, case FCRM-2026-0151 (`make demo`, 21 scenes) | **21 of 21**, 3 min 22 s | yes | `demo/demo-0151-final-21-of-21-passed.log` |
| Live browser scenes (`npm run test:e2e:live`) | **12 of 12** | none | `live-scenes-html/index.html` |
| Python unit tests with coverage (`make coverage`) | 8,883 passed, 266 skipped, coverage 91% (lines 92.9%, branches 83.4%) | none | `python-coverage.log` |
| Web unit tests (`npx vitest run`) | 344 passed | none | console only |

Screenshots: the mock and live reports hold one per test (`screenshot: 'on'`), the sweep one
per page and role (`sweep/screens/`, 347 files, reduced to 128 colours to keep the repo small).

## Run order matters

- The sweep needs a freshly seeded stack with **nothing run before it**: its "case with no run"
  checks (E1, E2) assume case 0151 has no run yet.
- The demo needs a freshly seeded stack too (`make seed`, then wait until the policy service
  answers). Its audit chain is append-only, so a second demo on the same data waits on the first
  run's records. The live scenes then need the demo to have finished, because they read the case it closed.
- So: `make seed` → sweep; `make seed` → demo → live scenes. The mock scenes need no stack.

## What was fixed to get here

Real defects (product):
- Primary buttons were white on `#09a78d` (3.0:1, WCAG AA needs 4.5:1): now the deep teal
  `#06705f` (6.0:1). Ten pages failed the accessibility scan on this.
- The harness monitor's scrolling log could not be reached by keyboard: now focusable and labelled.
- Opengrep findings (D-133): a cross-origin guard in the web client, entity-safe parsing of uploaded
  DOCX and XLSX, http(s)-only ops probes. See the earlier SonarQube and Opengrep reports.

Stale or fragile tests (test code only, no product change):
- Demo scene 12: after D-129 the analyst must resolve the owner's answers before review (added).
- Demo scene 6: waits for the required-field count, re-reads intake questions that were replaced
  under it (404), and decides a change type the intake agent proposes late.
- Sweep: the product owner's home is `/po`, not `/`; the observer sign-out check read the page
  before it was drawn; the keyboard check judged the last Tab stop before a 150 ms style transition
  had settled; the committee-pack live scene looked for wording the UI no longer has.

## Why the live demo is not guaranteed to pass

It uses real models, and passing needs the agents to stay inside limits. Nine runs are kept in
`demo/earlier-runs/`: one passed before the test fixes, seven stopped at different scenes, and the
final one passed 21 of 21.

- **Token budget (run d):** the case used 208,760 of its 250,000-token cap (a clean run targets
  120,000) after retries and long answers, so the budget guard refused the narrative agent. This is
  the guard working as designed (`per_case_budget_tokens`, `ai/models/tiers.yaml`).
- **Late change type (run g):** the intake agent's second pass proposed a fourth change type after
  the owner had settled three. The required-field count stays empty until the owner decides (by design).
- **Wrong domain on vendor controls (run c):** the assessment agent labelled CTL-T01 to T05 with a
  domain other than `channel`; the harness drops such controls (rule: the inventory owns the domain).
- **Slow Bedrock (runs i, and a slow start):** an intake call took about 5 minutes instead of seconds
  and the policy clause embedding timed out, past the tests' 90 to 300 s waits.
- **Audit 401 (run h):** the harness's audit writes were refused for about a minute with
  "Invalid service token", then recovered. The cause is not proven; the token cache renews by
  monotonic time while the token expires by wall-clock time, so a clock step (this is WSL2) would do it.
  A single retry with a fresh token on a 401 would harden it; it was not changed.

## Cost

`ops/token-analysis/runtime/2026-11-25.md`: about USD 1.12 of agent tokens across all runs, plus the
clause embedding at each API start (cents). Approved ceiling: USD 2.

## Setup notes

- The MongoDB volume dated from 19 October, so its demo users kept an old password hash and login
  answered 401; `make seed` reloaded it.
- Playwright needs its Chromium: `npx playwright install chromium`.
- Stop the stack with `make down` (data stays) or `make down-v` (deletes it).
