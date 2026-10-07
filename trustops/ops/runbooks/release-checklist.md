# Release checklist (the freeze)

Submission is 9 October 2026. Work through this list on the commit you intend to tag. Record the result of each line (command, date, output) in the release ADR. The first three sections run on a laptop with no stack; the rest need Docker and AWS credentials.

Gates referenced here are in `docs/governance/gates-register.md`: G-C1 (commit gate), G-T1 (eval gate), G-T2 (release gate), G-P1 (clean-clone proof).

## 1. The tree is the tree you mean to ship

```bash
git status --short                       # empty
git log --oneline -5
git ls-files | grep -E '(^|/)\.env'      # only .env.example files (trustops/.env.example, trustops/src/frontend/.env.example)
git grep -nE 'AKIA[0-9A-Z]{16}' -- .     # no output: no AWS access key id in any tracked file
```

Never commit `Hackathon.code-workspace`, a `.env`, `jwt_keys/` or an eval result that carries a model id (`evals/` holds labels only, ADR-04).

## 2. G-C1 and the generated files

```bash
make check                               # ruff, format, mypy, OPA check and tests, unit tests, env contract, web typecheck and vitest (about 6 minutes)
make openapi && git diff --exit-code docs/architecture/openapi/api.json    # the committed export is current (D-38)
python3 ai/_tools/compute_locks.py --check                                 # the agent, tool, skill and prompt locks match ai/
uv run --project src/backend --all-packages python ai/_tools/validate_ai.py     # 802 checks on the ai/ layer, incl. no model id outside tiers.yaml (not part of make check)
```

On the 6 October baseline `make check` was green: 7,094 unit tests passed and 4 skipped (538 integration-marked tests deselected), 120 of 120 Rego tests, 144 web tests, 606 source files type-checked, 925 files formatted.

## 3. Defects and gaps

The release gate (G-T2) needs no open P1. The defect trail is append-only, so a fixed defect is closed by a later row that says "Resolves D-nn", never by editing the old row, and a plain grep for "Open" over-reports. This scan reads the closing rows; it prints candidates for a person to judge:

```bash
python3 - <<'PY'
import re
rows = [[c.strip() for c in l.strip().strip("|").split("|")]
        for l in open("docs/governance/defect-trail.md", encoding="utf-8") if re.match(r"^\| D-\d+ ", l)]
closed = set()
for r in rows:
    for m in re.finditer(r"(?:Resolves|Supersedes|Closes)\s+((?:D-\d+(?:,\s*|\s+and\s+)?)+)", r[3] + " " + r[5]):
        closed.update(re.findall(r"D-\d+", m.group(1)))
done = ("Fixed", "Resolved", "Amended", "Closed by design", "Will not be carried forward")
for r in rows:
    if r[2].startswith("P1") and r[0] not in closed and not r[5].lstrip("*").startswith(done):
        print(r[0], r[2], "|", r[5][:70])
PY
```

On 6 October (at `84212a4`) it printed D-04 (vote secrecy: S5's HTTP layer hides votes and BB-05 tests it, but no closing row exists) and D-50 (process: the build machine slept with the lid closed; a user action). D-46 and D-47 no longer print: D-54 resolves them. Close D-04 with a new row that cites the evidence; decide D-50.

Re-read `docs/governance/known-gaps.md` and make sure every line is still true. A gap you fix gets a new dated row there (the governance hook refuses edits to existing table rows), not a deletion.

## 4. G-T2: the push gate

```bash
make gate        # check, make test-int (testcontainers: needs Docker), fcrm_evals build --check, fcrm_evals selfcheck
```

`make gate` ends by printing which pieces are not wired (contracts diff, semgrep; the live eval gate needs a live run). `make test-int` runs the 464 tests under `tests/integration`; set `FCRM_TEST_MONGO_URI` to a throwaway replica set to use your own Mongo (see CLAUDE.md). The other 74 integration-marked tests live in `tests/unit/**/*_live.py` and no make target runs them; run them too: `FCRM_TEST_MONGO_URI=... uv run --project src/backend --all-packages pytest -m integration tests/integration tests/unit`. On 6 October that command, against a throwaway replica set and no stack, gave 525 passed and 14 skipped in 192 to 225 s (7 skips need `FCRM_TEST_RABBITMQ_URL`, 6 are the named black-box skips, 1 is a missing real-document fixture).

`make e2e` plays the demo case by script and then runs the live rule tests and the Playwright scenes that read a finished demo. It calls Bedrock (about USD 0.47 a cycle on the committed chain, an estimate), so it needs the operator's approval first. Without any AI call, run:

```bash
cd src/frontend && npm run test:e2e && cd ../..        # Playwright scenes against the in-browser mock backend (4 specs)
cd src/frontend && npm run test:e2e:sweep && cd ../..  # every screen as all four roles on the running app, no AI calls, about 9 minutes
```

and drive the demo by hand through the web UI on the live stack (RUNNING.md, "Drive the demo by hand"), saving the screenshots and the final `GET /api/fcrm/audit/verify/FCRM-2026-0151` answer.

## 5. G-T1: the eval gate

The live gate is run on demand. Run it on the commit you tag, on the agents whose prompt, model or budget changed since the last committed run, or on all nine:

```bash
make evals EVALS_ARGS="--baseline"                # all nine agents, 5 rows each, Bedrock judge: about USD 1 projected on the committed chain, not measured (USD 3.14 and 7-11 minutes on 6 October with an Opus judge)
make evals-gate AGENT=research                   # per agent; exit 1 on FAIL
```

Commit `evals/results/<date>/` in the same commit as the change that needs it. `--publish` also loads the run into the stack's Mongo so Assurance, Agent quality shows it; it needs `--mongo-uri` or `MONGODB_URI` pointing at a Mongo that may write `fcrm_evals` (the root user, after `make up-debug`). The committed baseline of 6 October passes 8 of 9 gates on the 6 October chain (Sonnet 5.5 on mid and strong, Opus 5.5 as the critic and the judge); no baseline exists for the committed chain, whose judge is Nova Pro. Assessment fails at `li_within_one` 0.70 against 0.85 and says so (`evals/README.md`). State that in the release notes; do not change the threshold.

## 6. G-P1: the clean-clone proof

On a machine, or a scratch directory, with no `.env` and no `jwt_keys/`:

```bash
git clone <the repo> /tmp/trustops-proof && cd /tmp/trustops-proof/trustops
make bootstrap env keys                  # uv sync, hooks, images; .env; JWT keys
open -e .env                             # add AWS credentials (never print them)
make up                                  # 8 containers healthy; api needs ~85 s after the build
make seed                                # reload the seed, stage the demo PDFs
make ps
curl -s http://localhost:8080/healthz; echo
```

Then log in as each persona, drive the demo case, and check the chain. Record: time to healthy, the live composite before and after the override, the vote outcome, the audit record count, the token total the monitor shows (`/monitor/FCRM-2026-0151`), and `GET /api/fcrm/audit/verify/FCRM-2026-0151`. `make seed-verify` checks the seed through the gateway, and `make demo` plays the case by script (it calls Bedrock, so it needs the operator's approval); a hand-driven run watched by a person remains the proof a reviewer will ask for.

## 7. Documents

- `README.md` Status and `RUNNING.md` match what you just ran. Update the numbers from the files, not from memory.
- `docs/governance/sdlc-evidence.md`: re-run its count commands and update the dates.
- A release ADR in `docs/governance/decision-log.md` (append a row): the commit, the gate results, what is knowingly not done.

## 8. Tag

```bash
git tag -a v1.0-genius-hacks -m "TrustOps, Genius Hacks Q3 2026 submission"      # (proposed name)
git push origin main v1.0-genius-hacks
```

No commits after the deadline. If something must change after the tag, make a new commit and a new tag; never move a tag.

## Checked and not checked

- Run on 6 October 2026: `git ls-files | grep`, the AKIA grep (no match), the P1 grep (returns D-46 and D-47), `compute_locks.py --check` (green), `validate_ai.py` (802 passed, 0 failed under the project environment), `make openapi` (clean diff, 110 paths), `make check` (green).
- Not run: `make gate` as a whole, `make test-int`, `npm run test:e2e`, `make evals`, and the clean-clone proof. The `git push` and the tag are for the operator.
