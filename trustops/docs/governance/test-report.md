# Testing report

Written on 7 October 2026. It says what was tested, what passed, what failed, and what has not been tested yet. Every result names the day it was run and, where it matters, which set of AI models was in use. Nothing here is rounded up: a test that was not run on the final code says so.

The numbers come from commands and committed files: the last full `make check`, the drill reports in `ops/reports/resilience/`, the browser sweep's own report (`src/frontend/test-results/sweep-report.json`, produced by each run and not committed) and the defect list in `defect-trail.md`.

## In one page

| Test level | Result | When |
|---|---|---|
| Code checks (every commit) | **Pass**: 7,689 code tests (4 skipped), 164 web tests, 121 access-rule tests, 813 AI-layer checks, settings check ok | 7 Oct, final code |
| Database tests (real MongoDB) | **Partly**: 525 passed, 14 skipped; the clean-up work re-ran the case, common and audit parts; the whole set was not repeated on the final code | 6 Oct |
| Kill-and-restart drills | **Pass**: 12 of 12 on the live app; they found two real problems, both fixed | 7 Oct |
| Live demo, 21 scenes, real AI | **Partly**: 21 of 21 on the earlier model set; on the current set two runs stopped at scene 8 and scene 9 and it has not been repeated | 6 Oct |
| Live rule tests (6) and browser scenes (12) | **Not run on the final code**: they need a finished demo case; they passed on an earlier build | 6 Oct |
| AI quality tests (evals) | **Partly**: 8 of 9 gates on the earlier model set; a one-row check on the current set ran end to end (7 of 9) and shows the test system works; it is not a quality result | 6 Oct |
| Browser sweep of every screen (new) | **Failed, fixes in progress**: 32 of 43 tests passed; every one of the 11 failures traces to a real defect (D-105 to D-110) | 7 Oct |

## The browser sweep

**What it did.** A Playwright suite (`src/frontend/e2e/sweep-live/`) opened every screen of the running application as each of the four roles (product owner, analyst, committee member, observer) and as a signed-out visitor. It clicked every tab and every button that is safe to click, tried every role against the other roles' screens, scanned the product owner's pages for restricted words, checked sign-in and sign-out, tabbed through every page, and ran an accessibility scan on each. It made **no AI calls**: it never uploaded a document, submitted a case, asked the examiner, searched the policy corpus, voted, or published a configuration. The three actions it did change were tidied up in the same test: a new draft case was discarded, an integration was connected and disconnected, and one configuration draft was opened in the impact preview and discarded.

**How long, and how to run it.** About nine minutes, 43 tests, about 27 sign-ins:

```bash
cd src/frontend && npm run test:e2e:sweep
```

One area only: `npx playwright test --config e2e/playwright.sweep.config.ts 02-fences`. The spec files are `01-routes`, `02-fences`, `03-controls`, `04-desired`, `05-signin` and `06-a11y`. Each run replaces `test-results/sweep-report.json` and the screenshots under `test-results/screens/<role>/`.

**What it checked, in numbers.** 672 checks: 518 passed, 38 failed, 14 not run, 102 informational (disabled controls, fields left alone, stub buttons). On 118 distinct pages there were **no server errors and no crashes**.

| Role | Screens | Tabs | Buttons | Access fences | Sign-in | Keyboard | Accessibility | Expected behaviour |
|---|---|---|---|---|---|---|---|---|
| Product owner | 7 / 0 / 0 | 30 / 0 / 0 | 31 / 0 / 3 | 26 / 0 / 0 | 3 / 0 / 0 | 9 / 0 / 0 | 10 / 8 / 0 | none |
| Analyst | 25 / 1 / 0 | 64 / 0 / 0 | 144 / 3 / 7 | 9 / 0 / 0 | 3 / 0 / 0 | 23 / 0 / 0 | 29 / 16 / 0 | 1 / 3 / 0 |
| Committee | 5 / 0 / 0 | 12 / 0 / 0 | 18 / 0 / 4 | 17 / 0 / 0 | 4 / 0 / 0 | 4 / 0 / 0 | 4 / 4 / 0 | none |
| Observer | 4 / 0 / 0 | 0 / 0 / 0 | 1 / 0 / 0 | 24 / 1 / 0 | 3 / 0 / 0 | 1 / 0 / 0 | 1 / 0 / 0 | 0 / 1 / 0 |
| Signed out | none | none | none | 5 / 0 / 0 | 1 / 1 / 0 | none | none | none |

Each cell reads passed / failed / not run.

**What passed cleanly.**
- Every route and every tab of every role loads.
- The access rules hold in both directions. Each role is sent home by the app, or gets the "not in your view" page, on every screen that is not theirs. The API answers 403 for the other roles' endpoints, 401 with no sign-in, and 404 for another owner's case.
- The product owner never saw a score, band, rating, critic finding, vote or monitor text: 129 pages (the dashboard, the queue, integrations, and 21 cases times six tabs) and every answer those pages received were scanned.
- Sign-in for each role, a reload on a deep link, sign-out ending the session, and the return-to-page link all work.
- Keyboard use: every page can be reached with Tab, focus is visible, the skip link comes first, there are no traps, and every control has a name a screen reader can read.

**What failed.** Seven defects, found by the sweep and recorded in `defect-trail.md`:

| Id | Where | What happened | Should be |
|---|---|---|---|
| D-105 | Analyst, Activity tab (the most serious) | Pressing **Verify chain** replaces the whole page with "Something went wrong". The API answers `record_count`, `ok`, `first_break_seq`; the screen reads `count`, `head_hash`, `first_broken_seq` and fails on the missing hash. | A line such as "9 records, chain verified" |
| D-106 | Monitor (analyst, committee, observer) and the analyst Recommendation tab | A case with no AI run, which is every sample case and the example case before its first upload, shows a red error: "This case is not in your view" or "the narrative has not drafted the recommendation yet", with a Retry button. The API is right; the screen presents an expected empty state as a failure. | A calm "No run is recorded for this case yet" with no Retry |
| D-107 | Analyst, Agent quality tab | Always errors: the screen sends a date (`2026-09-28`), the API wants a week number (`2026-W40`). With a valid week and no published evaluation, the API answers 404, which the screen would also show as an error. | The quality table, or "no evaluation published for this week yet" |
| D-108 | Sign-in form | A wrong password shows "Sign-in failed. Your session has ended. Sign in again." The API's own answer is a clean "Unknown user or wrong password." | A message saying the user name or password is not correct |
| D-109 | Observer | The guide says the observer sees the harness monitor and Agent quality. The API allows it; the screens put Agent quality under the analyst-only area, so the observer is sent back to the monitor. | The guide and the screens agree |
| D-110 | Every table, the analyst dashboard chart, the committee pack | Table header text is too pale (colour contrast) on 28 pages; the dashboard chart can be tabbed into while hidden; a heading is out of order on the committee pack and the monitor error page has no heading. | Readable contrast and correct headings |
| D-111 | Product owner, closed sample cases (0118, 0133, 0128) | The Conditions tab says "No decision yet" under a "Closed" banner, because the sample data holds no outcome for them. | The recorded outcome, or an honest empty note |

Not defects, left as they are: four buttons that only show "not available in this build" (Export CSV on Overrides, Import CSV on Control inventory, Upload source on Policy corpus, Export case record on Activity); the sign-in token in the live-stream web address, which is the documented gateway design (the sweep's report hides it); disabled controls on sample cases.

**What the sweep could not reach**, and why:
- Needs your approval because it calls the AI service or changes what future cases use: uploading documents or "Use the demo's two files", importing from SharePoint, Ask the examiner, the Policy tab's search box, casting a vote, publishing a configuration version, previewing the FATF sync (it writes a draft), confirming a fast lane, submitting, locking, releasing, "Complete by hand".
- The monitor's counters, guard strip, log filters, Pause scroll and Copy log never appear, because no sample case has a run. One real run is needed to see them.
- The example case's analyst tabs do not exist before the case is submitted, so they could not be swept.
- A side effect to know: opening Risk and Controls as a case's own analyst records that the review has started, once. The sweep did that on case 0138, which is already in the committee stage.

## The other levels, in detail

**Code checks.** Run by `make check` before every commit. The last full run was on the final code of the model change (7 Oct): style and type checks clean, 7,689 code tests passed and 4 skipped, 121 access-rule tests, 813 AI-layer checks, the settings check, and 164 web tests. The same checks pass on every commit through the git hooks, which test what each commit changes.

**Database tests.** The code against a real MongoDB on a throwaway copy: 525 passed, 14 skipped on 6 October; the skips are 7 that need a message queue, 6 named black-box skips and 1 missing sample document. After the clean-up changes only the case, common and audit parts were run again (all passed). The whole set has not been run on the final code.

**Kill-and-restart drills.** Twelve drills stop, kill or restart the workers, the message queue, the database and the API in the middle of work, and check that nothing is lost or repeated. All 12 pass on the live application, across six sessions. The sessions found two product defects (an access policy that a restart never reloaded, D-92, and a step retry window shorter than an API restart, D-93) and a drill assumption (D-94); all fixed. Each session's report is committed in `ops/reports/resilience/`; full details in `docs/architecture/resilience.md`.

**Live demo.** The example case from the product owner's first upload to the committee outcome, with real AI agents: 21 of 21 scenes on the earlier model set (6 October, 197 audit records with a verifying chain, no stuck messages). On the current models (Sonnet 4.5, no Opus) two runs stopped at scene 8 and scene 9 and it has not been repeated (D-95, D-100). This needs your approval to run, at about USD 0.7 to 1.0.

**AI quality tests.** Nine agents scored against fixed examples with a judge model: 8 of 9 gates pass on the earlier models (the ninth, assessment, is capped by the examples; D-56). On the current models only a one-row check was run (7 of 9, about USD 0.44); it proves the test system and its dashboard work and is not a quality result. No quality baseline exists for the current models (D-100).

## How to run each level again

| Level | Command | Costs money |
|---|---|---|
| All code checks | `make check` | No |
| Database tests | `make test-int` (needs a throwaway MongoDB; see `RUNNING.md`) | No |
| Kill-and-restart drills | `make resilience DISRUPTIVE=1 AWS=1` | A few cents |
| Live demo | `make demo` | Yes, about USD 0.7 to 1.0 |
| Live rule tests and browser scenes | `make e2e` (runs the demo first) | Yes |
| AI quality tests | `make evals` | Yes, about USD 3 on the earlier models |
| Browser sweep | `cd src/frontend && npm run test:e2e:sweep` | No |

## What is still open

1. **The seven sweep defects** are being fixed. After the application is rebuilt (`make up`) the sweep is run again and this report is updated with the result.
2. **A complete live pass on the current models** (D-95, D-100) needs your approval: one `make e2e`, then `make evals` for a new quality baseline.
3. Heavy-load speed and other browsers were not part of this build and were not tested.
