# End-to-end test coverage and test cases

Written 26 November 2026 (ADR-90). One document for: what the browser and end-to-end tests covered, and every test case with its steps and expected result. Results are from the final run, in which everything passed. Counts and names come from the test files themselves (`npx playwright test --list`, `tests/e2e/test_demo_0151.py`).

## 1. Summary

| Suite | Cases | Result | Needs | Report |
|---|---|---|---|---|
| Mock-backend browser scenes | 8 | **8 passed** | the web app only (in-browser mock backend, seed data) | `mock-scenes-html/index.html` |
| Live browser scenes | 12 | **12 passed** | running stack, demo finished | `live-scenes-html/index.html` |
| Browser sweep (every screen, every role) | 43 | **43 passed** (582 checks, 0 failed, 107 notes, 32 controls not exercised) | running stack, freshly seeded, demo not yet run | `sweep/sweep-report.json`, `sweep/screens/` |
| Core-flow demo (case FCRM-2026-0151, five personas, real agents) | 21 | **21 passed** | running stack, fresh seed, Bedrock | `demo/demo-0151-final-21-of-21-passed.log` |
| Live black-box rules (`tests/e2e/test_bb_live.py`) | 6 | part of `make e2e`; not run in this session | running stack, a completed demo | n/a |
| Load test (Locust, `tests/load/locustfile.py`) | 4 user types | **passed**: 11,215 reads, 0 failures, p95 610 ms at 30 users | running stack, no Bedrock | `../locust/` |
| Python unit tests | 8,883 passed, 266 skipped | passed, coverage 91% (lines 92.9%, branches 83.4%) | nothing | `python-coverage.log` |
| Web unit tests | 344 | passed | nothing | n/a |

The mock and live reports hold a screenshot per test; the sweep stores a screenshot per page and role.

## 2. What is covered, by persona

Two kinds of test cover a persona: **browser** tests (Playwright, what the persona sees and can reach) and the **API demo** (the persona's journey, step by step, over HTTP through the real gateway). Playwright does **not** click through a whole journey; see section 2.1.

| Persona | Browser tests (Playwright) | Journey in the API demo |
|---|---|---|
| Product owner (Priya Nair) | Sign-in and landing on `/po`; every screen and case tab; no score, band or rating on any page; 403 on the harness monitor; turned away from analyst and committee routes; creating a draft case and discarding it; connecting and disconnecting an integration; reload on a deep link; sign-out | Scenes 1 to 7 and 10, 11, 19: open the case, upload two PDFs, confirm change types, reuse precedent values, answer gaps, submit, answer the analyst's questions with a document, read the plain outcome |
| Analyst (Rahul Iyer) | Sign-in; dashboard and queue; opening a case into the workbench; every workbench tab; harness monitor; account menu; configuration draft to its impact preview (discarded); turned away from other roles' routes | Scenes 7 to 16, 20: triage, review of every item, clause accepts, override with a reason, narrative edit and lock, pack build and release, examiner |
| Committee (Ana Mendes, Joseph Okafor, Lin Chen) | Sign-in; queue; opening a pack; Vote tab shows only voted or not voted; turned away from other routes | Scenes 16 to 18: pack released, a question answered as an addendum, three hidden votes then the reveal and outcome |
| Observer (Panel Observer) | Sign-in lands on the harness monitor of the demo case; read-only monitor; no other screen opens; sign-out | Scene 21: the monitor shows the records, the agents and the tokens within the cap |
| Anonymous | A signed-out deep link goes to sign-in with no data and returns to the page after sign-in; wrong password gives a clear message | n/a |

### 2.1 Not covered by Playwright

- **State-changing journeys by clicking.** The owner uploading and submitting, the analyst accepting clauses, overriding and locking the narrative, and the committee casting votes run in the API demo, not through the UI. The sweep deliberately leaves controls that start agent runs (uploads, submit, ask, search, publish) as 'not exercised'.
- **The AI's output quality.** That is the evals suite (`make evals`), not these tests.
- **Other browsers and devices.** Chromium only.

## 3. Run order and set-up

- Sweep: `make seed`, then run it with nothing before it (checks E1 and E2 assume case 0151 has no run).
- Demo: `make seed`, wait until the policy service answers, then `make demo`; then the live scenes (they read the case the demo closed).
- Mock scenes: no stack needed (`npm run test:e2e` in `src/frontend`).
- Commands in `src/frontend`: `npm run test:e2e`, `npm run test:e2e:live`, `npm run test:e2e:sweep` (live and sweep read `E2E_BASE_URL`, default `http://localhost:8080`). Demo: `make demo` in `trustops/`.
- Sign-in in the browser uses the seeded-user picker (no password); the demo uses the demo password through the API. Personas: Priya Nair (owner), Rahul Iyer (analyst), Ana Mendes, Joseph Okafor, Lin Chen (committee), Panel Observer.

## 4. Test cases

### 4.1 Mock-backend browser scenes (`src/frontend/e2e/scenes/`)

Run against the app's dev server with an in-browser mock backend fed from the seed. No stack, no AI. All 8 passed.

| ID | Persona | Test | Steps | Expected | File |
|---|---|---|---|---|---|
| MS-01 | Analyst | the analyst queue lists real cases and opens one into the workbench | Sign in as Rahul Iyer through the seeded-user picker; open the queue; click a case row | The queue lists cases from the seed; the row opens the case workbench | `analyst-queue.spec.ts` |
| MS-02 | Committee | a committee member opens the committee queue and a case s pack | Sign in as Ana Mendes; open the committee queue with the filter set to all; open a case's pack | A pack opens and its page mentions votes without a live tally | `committee-pack.spec.ts` |
| MS-03 | Product owner | the PO (Priya Nair) signs in and lands on the PO dashboard | Open the login page; choose Priya Nair in the picker | Lands on the PO dashboard | `login-and-persona-routing.spec.ts` |
| MS-04 | Analyst | the analyst (Rahul Iyer) signs in and lands on the analyst dashboard | Open the login page; choose Rahul Iyer | Lands on the analyst dashboard | `login-and-persona-routing.spec.ts` |
| MS-05 | Committee | a committee member (Ana Mendes) signs in and lands on the committee queue | Open the login page; choose Ana Mendes | Lands on the committee queue | `login-and-persona-routing.spec.ts` |
| MS-06 | All | "Switch user" is a full re-login through the real login page, never a client-side toggle | Signed in, use Switch user | A full re-login through the real login page, never a client-side toggle | `login-and-persona-routing.spec.ts` |
| MS-07 | Product owner | the PO dashboard renders its real KPIs and no restricted score/band text anywhere on the page | Sign in as Priya Nair; read the whole dashboard page text | KPIs render; no score, band or rating text appears anywhere on the page | `po-dashboard-no-score-leak.spec.ts` |
| MS-08 | Product owner | the PO cannot open the harness monitor route at all (403, not a redirect) | As the PO, go to the harness monitor route | A 403 page, not a redirect | `po-dashboard-no-score-leak.spec.ts` |

### 4.2 Live browser scenes (`src/frontend/e2e/scenes-live/`)

Run against the real running stack (gateway, identity, services, database). They make no AI calls, but read the case the demo closed. All 12 passed.

| ID | Persona | Test | Steps | Expected | File |
|---|---|---|---|---|---|
| LS-01 | Analyst | the queue lists real cases and opens one into the workbench | Sign in as Rahul Iyer through the real gateway and identity service; open /analyst/queue; open a case row | Seeded cases are listed; the row opens the workbench | `analyst-queue.spec.ts` |
| LS-02 | Analyst | the demo case is in the queue once the demo has closed it | As Rahul, open the queue under All and Closed | Case FCRM-2026-0151, closed by the demo, is listed (needs the demo to have finished) | `analyst-queue.spec.ts` |
| LS-03 | Committee | an open pack shows its votes as hidden until all three are cast | Sign in as Ana Mendes; open /committee?filter=all; open case 0138 or 0145; open the Vote tab | 'n of 3 votes in'; three member cards each show only Not yet voted or Voted · hidden, never a choice | `committee-pack.spec.ts` |
| LS-04 | Observer | Panel Observer sees the monitor of the demo case with its counters, guards and log | Sign in as Panel Observer (lands on the monitor of 0151) | Heading, the case's real counters, the seven guards and the log are shown; read-only | `harness-monitor.spec.ts` |
| LS-05 | Analyst | Rahul Iyer sees the monitor of the demo case with its counters, guards and log | Sign in as Rahul; open /monitor/FCRM-2026-0151 | Same monitor content, read-only | `harness-monitor.spec.ts` |
| LS-06 | Product owner | Priya Nair signs in and lands on the home of their role | Real login as Priya Nair | Lands on /po with 'Welcome back, Priya' | `login-routing.spec.ts` |
| LS-07 | Analyst | Rahul Iyer signs in and lands on the home of their role | Real login as Rahul Iyer | Lands on /analyst with the account menu for Rahul | `login-routing.spec.ts` |
| LS-08 | Committee | Ana Mendes signs in and lands on the home of their role | Real login as Ana Mendes | Lands on /committee with the 'Committee queue' heading | `login-routing.spec.ts` |
| LS-09 | Observer | Panel Observer signs in and lands on the home of their role | Real login as Panel Observer | Lands on the harness monitor of the demo case (ADR-19) | `login-routing.spec.ts` |
| LS-10 | Product owner | no page the product owner can open shows a score, a band or a rating | As Priya, open the dashboard, queue and the demo case's tabs (overview, documents, review, inbox, activity, conditions) | No score, band or rating text on any page | `po-no-risk-numbers.spec.ts` |
| LS-11 | Product owner | the closed demo case tells the owner the outcome in plain words, with its conditions | As Priya, open the closed demo case | The outcome is told in plain words with its conditions | `po-no-risk-numbers.spec.ts` |
| LS-12 | Product owner | the harness monitor is closed to the owner (403, not a redirect) | As Priya, go to the monitor route | 403, not a redirect | `po-no-risk-numbers.spec.ts` |

### 4.3 Browser sweep (`src/frontend/e2e/sweep-live/`)

Six spec files, 43 tests, run for the roles product owner, analyst, committee, observer and anonymous against the real stack. They make no AI calls. All 43 passed. Each test records many individual checks in `sweep/sweep-report.json` (721 entries).

| ID | Test | What it does | Expected | File |
|---|---|---|---|---|
| SW-01 | routes as po › every top-level screen of the po | Open each top-level URL the role may reach | Each page has a main landmark and a heading; no uncaught error or console.error; no 5xx; no 4xx outside the allow-list; no red error banner with Retry | `01-routes.spec.ts` |
| SW-02 | routes as po › every tab of the po's case pages | Open every tab of the case pages of several seeded cases of different stages and of case 0151 | Same page checks as above on every tab | `01-routes.spec.ts` |
| SW-03 | routes as analyst › every top-level screen of the analyst | Open each top-level URL the role may reach | Each page has a main landmark and a heading; no uncaught error or console.error; no 5xx; no 4xx outside the allow-list; no red error banner with Retry | `01-routes.spec.ts` |
| SW-04 | routes as analyst › every tab of the analyst's case pages | Open every tab of the case pages of several seeded cases of different stages and of case 0151 | Same page checks as above on every tab | `01-routes.spec.ts` |
| SW-05 | routes as committee › every top-level screen of the committee | Open each top-level URL the role may reach | Each page has a main landmark and a heading; no uncaught error or console.error; no 5xx; no 4xx outside the allow-list; no red error banner with Retry | `01-routes.spec.ts` |
| SW-06 | routes as committee › every tab of the committee's case pages | Open every tab of the case pages of several seeded cases of different stages and of case 0151 | Same page checks as above on every tab | `01-routes.spec.ts` |
| SW-07 | routes as observer › every top-level screen of the observer | Open each top-level URL the role may reach | Each page has a main landmark and a heading; no uncaught error or console.error; no 5xx; no 4xx outside the allow-list; no red error banner with Retry | `01-routes.spec.ts` |
| SW-08 | fences for the po › the po is turned away from the other roles' routes | Visit the other roles' routes | Redirected to the role's own home or shown a not-in-your-view/403 page, with no hint the route exists | `02-fences.spec.ts` |
| SW-09 | fences for the po › the API refuses the po the other roles' endpoints | Call the other roles' read-only endpoints with the page's own bearer token | 403 (never 401); 404 for a case that is not the owner's | `02-fences.spec.ts` |
| SW-10 | fences for the po › the PO sees nothing restricted on any page or case, and no restricted field in any answer | Visit every PO page and every owned case; inspect the JSON the pages receive | No score, band, rating, critic finding, vote or harness text, and no such field in any answer | `02-fences.spec.ts` |
| SW-11 | fences for the po › the PO cannot open the harness monitor (403 page) or another owner's case | As the PO open the harness monitor and another owner's case | A 403 page; the other owner's case answers not found | `02-fences.spec.ts` |
| SW-12 | fences for the analyst › the analyst is turned away from the other roles' routes | Visit the other roles' routes | Redirected to the role's own home or shown a not-in-your-view/403 page, with no hint the route exists | `02-fences.spec.ts` |
| SW-13 | fences for the analyst › the API refuses the analyst the other roles' endpoints | Call the other roles' read-only endpoints with the page's own bearer token | 403 (never 401); 404 for a case that is not the owner's | `02-fences.spec.ts` |
| SW-14 | fences for the committee › the committee is turned away from the other roles' routes | Visit the other roles' routes | Redirected to the role's own home or shown a not-in-your-view/403 page, with no hint the route exists | `02-fences.spec.ts` |
| SW-15 | fences for the committee › the API refuses the committee the other roles' endpoints | Call the other roles' read-only endpoints with the page's own bearer token | 403 (never 401); 404 for a case that is not the owner's | `02-fences.spec.ts` |
| SW-16 | fences for the observer › the observer is turned away from the other roles' routes | Visit the other roles' routes | Redirected to the role's own home or shown a not-in-your-view/403 page, with no hint the route exists | `02-fences.spec.ts` |
| SW-17 | fences for the observer › the API refuses the observer the other roles' endpoints | Call the other roles' read-only endpoints with the page's own bearer token | 403 (never 401); 404 for a case that is not the owner's | `02-fences.spec.ts` |
| SW-18 | fences for the observer › the observer can open the monitor and has no other screen | As the observer try every other screen | Only the monitor opens | `02-fences.spec.ts` |
| SW-19 | an unauthenticated visit to a deep link is sent to sign in, with no data | Open a deep link while signed out | Sent to sign-in with no data shown | `02-fences.spec.ts` |
| SW-20 | controls as po › every control on every page of the po | List the visible links, buttons and form controls on each page and click the safe ones (navigation, filters, sort headers, dialog and menu openers) | No errors; controls that start agent runs, send a case onward or change seeded data are listed as 'not exercised' | `03-controls.spec.ts` |
| SW-21 | controls as po › "New case" creates a draft and "Discard" removes it again | Fill the New case form, create one draft, then Discard it | The draft appears and is removed again | `03-controls.spec.ts` |
| SW-22 | controls as po › an integration is connected and disconnected again | Connect an integration, then disconnect it | Both work and the state is restored | `03-controls.spec.ts` |
| SW-23 | controls as analyst › every control on every page of the analyst | List the visible links, buttons and form controls on each page and click the safe ones (navigation, filters, sort headers, dialog and menu openers) | No errors; controls that start agent runs, send a case onward or change seeded data are listed as 'not exercised' | `03-controls.spec.ts` |
| SW-24 | controls as analyst › the analyst's account menu opens the harness monitor in a new tab | Open the account menu and choose the harness monitor | It opens in a new tab | `03-controls.spec.ts` |
| SW-25 | controls as analyst › a configuration draft opens its impact preview and is discarded, never published | Open a configuration draft to its impact preview and discard it | Preview shown; never published | `03-controls.spec.ts` |
| SW-26 | controls as committee › every control on every page of the committee | List the visible links, buttons and form controls on each page and click the safe ones (navigation, filters, sort headers, dialog and menu openers) | No errors; controls that start agent runs, send a case onward or change seeded data are listed as 'not exercised' | `03-controls.spec.ts` |
| SW-27 | controls as committee › the committee's account menu opens the harness monitor in a new tab | Open the account menu and choose the harness monitor | It opens in a new tab | `03-controls.spec.ts` |
| SW-28 | controls as observer › every control on every page of the observer | List the visible links, buttons and form controls on each page and click the safe ones (navigation, filters, sort headers, dialog and menu openers) | No errors; controls that start agent runs, send a case onward or change seeded data are listed as 'not exercised' | `03-controls.spec.ts` |
| SW-29 | E1: the harness monitor of a case with no run › analyst, a seeded case in review: the monitor shows information, not a red error with Retry | Open the harness monitor of a case with no run | An informational 'no run recorded' message under a heading; no red banner, no Retry (D-106) | `04-desired.spec.ts` |
| SW-30 | E1: the harness monitor of a case with no run › analyst, the example case 0151: the monitor shows information, not a red error with Retry | Open the harness monitor of a case with no run | An informational 'no run recorded' message under a heading; no red banner, no Retry (D-106) | `04-desired.spec.ts` |
| SW-31 | E1: the observer lands on the monitor of a case with no run › observer's home page (the monitor of 0151) shows information, not a red error with Retry | Open the harness monitor of a case with no run | An informational 'no run recorded' message under a heading; no red banner, no Retry (D-106) | `04-desired.spec.ts` |
| SW-32 | E2: the analyst Recommendation tab with no drafted recommendation › analyst, a seeded case in review: the Recommendation tab shows information, not a red error with Retry | Open the analyst Recommendation tab on a case with no drafted recommendation | An informational message ('No recommendation is drafted' / 'Not submitted yet'); no red banner, no Retry | `04-desired.spec.ts` |
| SW-33 | E2: the analyst Recommendation tab with no drafted recommendation › analyst, the example case 0151: the Recommendation tab shows information, not a red error with Retry | Open the analyst Recommendation tab on a case with no drafted recommendation | An informational message ('No recommendation is drafted' / 'Not submitted yet'); no red banner, no Retry | `04-desired.spec.ts` |
| SW-34 | the po signs in, lands home, survives a reload on a deep link, and signs out | Sign in, check the home page, reload on a deep link, sign out, then try the home address again | Home lands correctly; the reload keeps page and session; after sign-out the sign-in page shows and the session is gone | `05-signin.spec.ts` |
| SW-35 | the analyst signs in, lands home, survives a reload on a deep link, and signs out | Sign in, check the home page, reload on a deep link, sign out, then try the home address again | Home lands correctly; the reload keeps page and session; after sign-out the sign-in page shows and the session is gone | `05-signin.spec.ts` |
| SW-36 | the committee signs in, lands home, survives a reload on a deep link, and signs out | Sign in, check the home page, reload on a deep link, sign out, then try the home address again | Home lands correctly; the reload keeps page and session; after sign-out the sign-in page shows and the session is gone | `05-signin.spec.ts` |
| SW-37 | the observer signs in, lands home, survives a reload on a deep link, and signs out | Sign in, check the home page, reload on a deep link, sign out, then try the home address again | Home lands correctly; the reload keeps page and session; after sign-out the sign-in page shows and the session is gone | `05-signin.spec.ts` |
| SW-38 | a wrong password gives a clear message, not a stack trace | Type a wrong password on the password form | A clear message, not a stack trace | `05-signin.spec.ts` |
| SW-39 | a signed-out deep link goes to sign in and returns to the same page afterwards | Open a deep link signed out, sign in | Returns to the same page afterwards | `05-signin.spec.ts` |
| SW-40 | keyboard and accessibility as po › every page of the po: Tab, focus, names, axe | On every page press Tab through the controls, read accessible names, run an axe scan | Tab reaches every control with a visible focus change, skip link first, no focus trap; every control has a name; no serious or critical axe violation | `06-a11y.spec.ts` |
| SW-41 | keyboard and accessibility as analyst › every page of the analyst: Tab, focus, names, axe | On every page press Tab through the controls, read accessible names, run an axe scan | Tab reaches every control with a visible focus change, skip link first, no focus trap; every control has a name; no serious or critical axe violation | `06-a11y.spec.ts` |
| SW-42 | keyboard and accessibility as committee › every page of the committee: Tab, focus, names, axe | On every page press Tab through the controls, read accessible names, run an axe scan | Tab reaches every control with a visible focus change, skip link first, no focus trap; every control has a name; no serious or critical axe violation | `06-a11y.spec.ts` |
| SW-43 | keyboard and accessibility as observer › every page of the observer: Tab, focus, names, axe | On every page press Tab through the controls, read accessible names, run an axe scan | Tab reaches every control with a visible focus change, skip link first, no focus trap; every control has a name; no serious or critical axe violation | `06-a11y.spec.ts` |

### 4.4 Core-flow demo (`tests/e2e/test_demo_0151.py`)

Five personas drive case FCRM-2026-0151 from the owner's first upload to the committee outcome through the real gateway and stack, with real agents on Bedrock. Scenes run in order and share state; the first failure makes the later scenes skip. The scenes assert structure (not exact figures, which the live agents decide). All 21 passed in the final run.

| ID | Persona | Test (scene title) | Steps | Expected |
|---|---|---|---|---|
| DM-01 | Product owner | scene 1: the po dashboard shows the work and no risk numbers | Sign in; open the dashboard | Shows the work and no risk numbers |
| DM-02 | Product owner | scene 2: the new case has the pinned field set | Create the case | The new case has the pinned field set |
| DM-03 | Product owner | scene 3: two pdfs are scanned read and every value has a page and line | Upload two PDFs | Both are scanned and read; every value has a page and line; change types proposed |
| DM-04 | Product owner | scene 4: each change type is one gate and the required set resolves | Confirm or remove each proposed change type | One gate per change type; the required field set resolves |
| DM-05 | Product owner | scene 5: values are reused from the precedent with their provenance | Reuse values from the precedent | Values carry their provenance |
| DM-06 | Product owner | scene 6: gaps are answered until the threshold without blocking on a new fact | Answer gaps until the completion threshold | The threshold is reached without blocking on a new fact |
| DM-07 | Product owner / analyst | scene 7: a correction keeps the original four checks pass triage picks rahul | Correct a value and submit | The original four checks pass; triage picks Rahul |
| DM-08 | Analyst (agents work, nobody decides) | scene 8: screening research and assessment run and join with no human input | Wait while screening, research, assessment, critic and completeness run | They run in order and join with no human input; all five agents have a permit record |
| DM-09 | Analyst | scene 9: the score comes from s2 and only the cases analyst sees it | Read the score | It comes from the scoring service; only the case analyst sees it |
| DM-10 | Analyst / product owner | scene 10: one round of questions goes to the owner and the clock pauses | Send one round of questions to the owner | The owner is asked; the clock pauses |
| DM-11 | Product owner | scene 11: the owner answers with a document and v2 reruns the changed fields | Answer with a document | Version 2 reruns the changed fields; version 1 is unchanged |
| DM-12 | Analyst | scene 12: clauses are accepted one call each and a search can record a gap | Resolve the answers; accept the clauses one call at a time; search the policy and record a gap | No bulk accept; each decision has its own record; the case leaves clarification for review |
| DM-13 | Analyst | scene 13: controls ratings and critic findings are decided one at a time | Decide controls, ratings and critic findings one at a time | An override needs a reason; one record per decision |
| DM-14 | Analyst | scene 14: an override with a reason rescores and the acknowledgements follow | Override a control with a reason | The case is rescored and the acknowledgements follow |
| DM-15 | Analyst | scene 15: the narrative is edited locked and the pack is built | Edit and lock the narrative; build the pack | The pack is built and frozen |
| DM-16 | Analyst / committee | scene 16: the four checks pass the pack is released and tallies are hidden | Run the four checks and release the pack | Released; tallies hidden |
| DM-17 | Committee / analyst | scene 17: a committee question is answered as an addendum numbers unchanged | A committee member asks a question; the analyst answers | Answered as an addendum; numbers unchanged |
| DM-18 | Committee | scene 18: votes are hidden until three then reveal with the outcome | Three members vote; reveal | Votes hidden until all three are in; then the outcome shows |
| DM-19 | Product owner | scene 19: the po reads a plain outcome and a closed case takes no write | Read the outcome; try a write on the closed case | A plain outcome; a closed case takes no write |
| DM-20 | Analyst | scene 20: the examiner cites only the chain and a new list flags cases | Ask the examiner; list watchlist cases | Answers cite only the chain; a new list flags cases |
| DM-21 | Observer / analyst | scene 21: the monitor shows the records the agents and tokens within the cap | Open the harness monitor | Records, agents and tokens shown, within the cap |

### 4.5 Live black-box rules (`tests/e2e/test_bb_live.py`)

Run by `make e2e` after the demo, on the completed case. Not run in this session.

| ID | Rule |
|---|---|
| BB-03 | An override needs a reason and the audit chain keeps it |
| BB-06 | A locked pack is frozen and its versions stay readable |
| BB-08 | There is no bulk accept; each item has its own record |
| BB-09 | The owner's answer makes version 2 and version 1 stays what it was |
| BB-13 | Nothing is released before the acknowledgements |
| BB-14 | A case keeps the versions it pinned when a newer inventory is published |

### 4.6 Other automated tests (not browser)

| Kind | Where | Notes |
|---|---|---|
| Python unit tests | `tests/unit/` (mirrors the source folders) | 8,883 passed, 266 skipped, coverage 91%. Includes Hypothesis property tests (quote matching, guardrail rules, audit chain, ingestion) |
| Black-box rules, in process | `tests/integration/blackbox/` (13 files, BB-01, 02, 04, 05, 06, 07, 09, 10, 11, 12, 13, 14 twice) | Each product rule from `docs/requirements/rules.md` has at least one test id |
| Integration tests | `tests/integration/` (`contract`, `live_mongo`, `services`, `workers`, `packages`, `cross_package`) | Need Docker or `FCRM_TEST_MONGO_URI` (`make test-int`); not run in this session |
| Failure and restart drills | `tests/resilience/` | Kill and restart drills against the running stack (`make resilience`); not run in this session |
| Web unit tests | `src/frontend/src/test/` | 344 passed |

### 4.7 Load test (`tests/load/locustfile.py`, results in `ops/reports/locust/`)

Locust, reads only: no uploads, submits, searches, asks or votes, so it never starts an agent or calls Bedrock. Each simulated user logs in once with a demo user and browses with a random 1 to 3 second wait. The gateway allows 10 logins a minute per IP, so users start at 0.15 a second. Run: 30 users, 9 minutes, local stack.

| ID | User | Steps | Share |
|---|---|---|---|
| LT-01 | Product owner (5 demo users) | dashboard, queue; open a case and two of documents, review, inbox, activity | 40% |
| LT-02 | Analyst (3 demo users) | dashboard, queue (all); open a case's workbench and two of risk, assessment, policy, activity | 40% |
| LT-03 | Committee (2 demo users) | committee queue; a pack's latest and its votes | 15% |
| LT-04 | Observer | harness view of the demo case | 5% |

Pass criteria and result: no 5xx answer (0); failures under 1% excluding 429 (0%); aggregated p95 under 1.5 s (610 ms). Median 44 ms, about 21 requests a second, 0 answers of 429. Individual slow requests reached 4.5 s; the stack shares a WSL machine with other containers, so treat the numbers as indicative.

## 5. Where the written specs are

- Walkthrough (the 21-scene storyline): `docs/requirements/source/04-walkthrough.html`; its figures: `docs/requirements/research/06-walkthrough-seed-extraction.md`.
- Screen specs and test design: `docs/architecture/design/C-web-tests-ops-sdlc.md` (section 2 screens, section 3 tests).
- Rules and their test ids: `docs/requirements/rules.md`; personas: `docs/requirements/personas.md`.
- Earlier results and what needs approval to run: `docs/governance/test-report.md`.

## 6. How these cases were checked

The case names, files and counts above are read from the test files. The steps and expected results restate each test's own header comment and assertions in plain words; if a test changes, this document needs updating with it. Nine earlier demo runs, and why each stopped, are in `demo/earlier-runs/`.
