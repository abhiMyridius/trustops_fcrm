# Token analysis

How many tokens the nine agents use, what was wrong with the first design's budgets, what changed, and what is left. Every number below is read from a committed file; the file is named next to it. Cost is an estimate: tokens times the list prices in `ai/models/tiers.yaml`, which are verified (AWS Price List API, us-east-1, Regional rate card, retrieved 2026-10-06; each row names its source). Reconcile against the AWS bill.

**Note, 7 October 2026 (ADR-62, ADR-65): the committed model chain changed after this page was written.** The committed chain is Sonnet 5.5 on the mid and strong tiers, Haiku 4.5 and Nova, with no Opus. Nova Pro replaced Opus 5.5 on the judge tier (the critic and the eval judges), the judge budget went from 20,000 to 28,000 tokens an attempt, and the strong tier's retry model is Sonnet 5.5, where it was Opus 5.5. ADR-62 first committed Sonnet 4.5 on mid and strong; ADR-65 replaced that choice the same day, so Sonnet 5.5 is back and Sonnet 4.5 is in no chain. Every figure in sections 1 to 6 was measured on the 6 October chain: Sonnet 5.5 on mid and strong, and Opus 5.5 as the critic, the judge and the strong tier's first fallback. None has been re-measured on the committed chain, which differs from it in the judge tier and in that retry model; the measured tables are left as they were. The Sonnet 5.5 rows are back in the price table at the rates the dollar figures below used (2.20 in and 11.00 out, cache read 0.22, cache write 2.75, USD per million tokens, Regional rate card, us-east-1), so the Sonnet share of those figures can be recomputed from `tiers.yaml`. The Opus 5.5 rows (4.40 and 22.00) are not in it, so the Opus share cannot. The committed rates are Haiku 4.5 1.10 and 5.50, Sonnet 5.5 2.20 and 11.00, Nova Lite 0.06 and 0.24, Nova Pro 0.80 and 3.20. A Sonnet 5.5 token costs about two thirds of a Sonnet 4.5 token (3.30 and 16.50).

**Estimate for the committed chain, not a measurement (7 October 2026, ADR-65).** One complete cycle (one case from the owner's first upload to the committee outcome, every agent) costs about USD 0.47 (0.41 to 0.59, depending on the split between input and output tokens), about USD 0.34 for a clean run with no retries, and about USD 0.50 at the 160,000-token cap. It is the token counts of the last complete run (6 October) priced at the committed chain's verified rates. The same cycle on Sonnet 4.5 was estimated at about USD 0.65. A Nova Pro critic used 21,851 tokens on one live run against 16.4k on Opus 5.5, so a run on the committed chain may use more tokens than the counts below. A full `make evals` is projected at about USD 1; the 6 October run cost USD 3.14 with an Opus 5.5 judge. Neither projection has been measured.

**Dollar figures are recomputed.** The committed `index.json` files of the runs below were written at the earlier placeholder rates, so they show lower dollars (`price_status` in each). Every figure on this page is the run's recorded tokens at the verified prices. Every Anthropic rate is exactly 1.10x its placeholder (input, output, cache read, cache write), so an Anthropic call costs 1.10x what its file records, whichever models split a row. Rows that include Nova Lite (the cheap tier's fallback) are within USD 0.01 of that rule per run. The demo case's figure comes from the script's own total (no committed file records its per-call models), so it is the earlier figure of about 0.45 times 1.10, rounded. Tokens are unchanged.

Sources used throughout:

| Short name | File |
|---|---|
| tiers | `ai/models/tiers.yaml` |
| evals README | `evals/README.md` |
| run 1 | `evals/results/2026-10-05/` (`index.json`, `<agent>.live.json`) |
| run 2 | `evals/results/2026-10-06/` (`index.json`, `<agent>.live.json`, `gate.json`) |
| demo sum | `ops/scripts/demo_case_tokens.py`, as recorded in the evals README |

Scope: this is the runtime token story of the product's agents. Build-time tokens (Claude Code sessions that wrote the repository) are not measured: `make build-usage` is not wired and `ops/token-analysis/` does not exist.

## 1. The budgets as built, by agent

*Note, 7 October 2026 (ADR-62, ADR-65): the chain and budget columns below are those of 6 October. The mid (11,000) and judge (15,000) budgets shown were raised to 15,000 and 20,000 before 7 October (`docs/architecture/design/B-ai-layer.md` section 11). The committed chain is cheap Haiku 4.5 then Nova Lite (15,000), mid Sonnet 5.5 then Haiku 4.5 (15,000), strong Sonnet 5.5, Sonnet 5.5 again, then Nova Pro (24,000), and judge Nova Pro then Haiku 4.5 (28,000). The p50, p90 and max columns were measured on the 6 October chain. For the mid and strong rows the first attempt ran on Sonnet 5.5, which the committed chain uses again with the same prompts and `effort` low, but none of these columns has been re-measured on the committed chain, and a retry of research or assessment ran on Opus 5.5 then and runs on Sonnet 5.5 now. The critic row does not carry over: its p90 of 11,695 is an Opus 5.5 figure, and one live run of the critic on Nova Pro used 21,851 tokens and was blocked at 20,000 (D-95). 28,000 rests on that one observation.*

A tier's budget is what **one attempt** may spend, every request of it included (a tool round trip re-sends the request). It is set from the p90 of measured attempts times about 1.3 (tiers, evals README "Budgets from measured usage"). One retry per job (ADR-14), 120,000 tokens per case (tiers `per_case_budget_tokens`).

| Agent | Tier | Model chain (labels, attempt 1) | Budget per attempt | Attempts measured | p50 | p90 | Max | Demo-case job | Budget over p90 |
|---|---|---|---|---|---|---|---|---|---|
| screening | cheap | Haiku 4.5, Nova Lite | 15,000 | 14 | 3,219 | 3,570 | 3,598 | 4,212 | 4.2 |
| intake | cheap | Haiku 4.5, Nova Lite | 15,000 | 10 | 5,615 | 6,723 | 6,723 | 12,240 and 9,638 | 1.2 (on the demo window) |
| watchlist | cheap | Haiku 4.5, Nova Lite | 15,000 | 10 | 3,211 | 3,426 | 3,426 | 3,615 | 4.4 |
| narrative | mid | Sonnet 5.5, Haiku 4.5 | 11,000 | 10 | 4,880 | 8,386 | 8,386 | 5,744 and 6,638 | 1.3 |
| examiner | mid | Sonnet 5.5, Haiku 4.5 | 11,000 | 10 | 4,366 | 8,728 | 8,728 | 4,575 | 1.3 |
| policy_gap | mid | Sonnet 5.5, Haiku 4.5 | 11,000 | 2 | 6,537 | 6,537 | 6,537 | 6,885 | 1.7 |
| research | strong | Sonnet 5.5, Opus 5.5, Nova Pro | 24,000 | 11 | 17,085 | 17,891 | 18,110 | 11,666 (retry 23,609) | 1.3 |
| assessment | strong | Sonnet 5.5, Opus 5.5, Nova Pro | 24,000 | 10 | 10,074 | 17,639 | 17,639 | 14,887 | 1.4 |
| critic | judge | Opus 5.5, Sonnet 5.5 | 15,000 | 10 | 6,027 | 11,695 | 11,695 | 10,917 | 1.3 |

Attempt columns and the demo-case column: evals README (ten attempts of the two 6 Oct gate runs, input plus output). The chain and budgets: tiers. The critic runs on a different model than the assessor it checks (ADR-04). The cheap tier's budget is set by intake's real demo windows (6 to 12k an attempt), not by the gate sample. The five-row samples are small: an attempt that needs more than its budget is blocked (`agent.failed`, the handoff gate), never silently cut.

Wall clocks: cheap 60 s, mid 120 s, strong and judge 180 s (tiers). Output cap per request: 4,000 / 4,000 / 8,000 / 8,000 tokens (tiers).

## 2. What the agents used in the gate run

*Note, 7 October 2026 (ADR-62, ADR-65): measured at `7249d2a` on the 6 October chain (Sonnet 5.5 on mid and strong, the critic and the judge on Opus 5.5). The judge row is Opus 5.5; the committed judge is Nova Pro. No run of this shape exists on the committed chain.*

Run 2 (`make evals` at `7249d2a`: 5 rows per agent, 1 for policy_gap, seed 7, Bedrock judge). Agent tokens are input plus output as the `usage` block of each `<agent>.live.json` reports them; cache reads and writes are reported apart by Bedrock.

| Agent | Rows | Model calls | Agent tokens | Per row | Cache read | Cache write | Agent cost (USD) |
|---|---|---|---|---|---|---|---|
| screening | 5 | 5 | 17,801 | 3,560 | 0 | 0 | 0.034 |
| intake | 5 | 5 | 28,739 | 5,748 | 0 | 0 | 0.053 |
| watchlist | 5 | 5 | 15,948 | 3,190 | 0 | 0 | 0.021 |
| narrative | 5 | 5 | 24,781 | 4,956 | 20,790 | 0 | 0.035 |
| examiner | 5 | 6 | 25,513 | 5,103 | 7,698 | 0 | 0.057 |
| policy_gap | 1 | 1 | 6,526 | 6,526 | 5,595 | 0 | 0.008 |
| research | 5 | 10 | 86,397 | 17,279 | 61,074 | 8,826 | 0.147 |
| assessment | 5 | 6 | 53,605 | 10,721 | 35,004 | 0 | 0.145 |
| critic | 5 | 5 | 30,240 | 6,048 | 20,385 | 0 | 0.119 |
| **Agents, total** | 41 | 48 | **289,550** | | 150,546 | 8,826 | **0.619** |
| Judge (Opus 5.5, 132 calls, grades narrative, examiner and critic) | | 132 | 235,700 | | 0 | 0 | 2.525 |
| **Run total** | | | **525,250** | | | | **3.145** |

Reading it: the cheap tier is cheap in tokens as well as in price (screening and watchlist about 3.2 to 3.6k a row, no cache because their prompts stay under the Haiku cache minimum of 4,096 tokens, tiers `cache_min_prefix_tokens`). Research is the most expensive agent (it makes a tool call, so it sends its request twice; its one retry is in the 10 calls). Cache reads are 150,546 tokens, billed at a tenth of the input rate or less (prices of 6 October: 0.1x for Haiku and Sonnet 5.5, 0.05x for Opus 5.5). **The judge is 80% of an eval run's cost** (2.53 of 3.14 USD): it grades every row on faithfulness, relevancy and hallucination, three votes on the narrative.

## 3. What was wrong before

*Note, 7 October 2026 (ADR-62, ADR-65): run 1 was also measured on the 6 October chain (Sonnet 5.5 on mid and strong, Opus 5.5 as the critic and the judge). Finding 4 describes a failure of Sonnet 5.5 and Opus 5.5 under a halved retry allowance. The retry now keeps the tier's whole output allowance (section 4), and Sonnet 5.5 is in the committed chain again with that fix; Opus 5.5 is in no chain.*

The first live run (run 1, `make evals` at `4c715bb`, the system as shipped) passed 1 of 9 gates. The token findings (evals README, "What stops the agents", defects D-46 and D-47):

| # | Finding | Evidence |
|---|---|---|
| 1 | **The budgets could not hold a real run.** Screening spent 6.7k to 7.1k tokens against a 6k budget; intake 8.2k to 11.1k against 6k; research 22k to 26k against 20k. The live intake agent failed on the demo documents with `total_tokens_limit of 6000, total_tokens=7370` (D-46). The design had priced a screening run at 1.8k | run 1; defect trail D-46 |
| 2 | **A blocked row threw its answer away.** The runtime refused the call after the second response (`usage limit exceeded`), so a usable answer was discarded and the tokens were still spent. 21 of 25 cheap and strong rows were blocked this way | run 1; D-46 |
| 3 | **H2 sent the system prompt unrendered.** The Jinja template reached the model as text (`{{ config("routing.R4") }}`); rendered, the agents behaved better but spent about three times the tokens | evals README finding 7 |
| 4 | **The retry starved a thinking model.** Attempt 2 halved `max_tokens` (2,000 for the judge tier); Sonnet 5.5 and Opus 5.5 think adaptively and cannot switch it off, so a retried critic spent the 2,000 tokens thinking and returned an empty tool call. Four rows of run 1 ended this way | evals README finding 5 |
| 5 | **A refused attempt's tokens were not charged.** `AgentDone.usage`, which H1 sums into the case total, held the last successful attempt only; a retried narrative row reported `[0, 8386]` | evals README fix 10 |
| 6 | **Guard 4 failed closed on empty answers and citations did not resolve**, so rows were retried or blocked for contract reasons rather than model ones | evals README findings 2 to 4 |

Run 1 spent 608,534 agent tokens and about USD 2.16 on agents (730,715 tokens and USD 3.46 with the judge) and completed 7 of its 41 rows (screening 0 of 5, intake 0, research 0, assessment 1, critic 0, narrative 1, examiner 5, watchlist 0, policy_gap 0 of 1).

## 4. What changed

*Note, 7 October 2026 (ADR-62, ADR-65): measured on the 6 October chain. The effort setting in the sixth row applied to Sonnet 5.5 and Opus 5.5. The committed chain sets `effort` low on mid and strong, which Sonnet 5.5 takes and Haiku and Nova reject; Opus 5.5 is out. The budgets in the first row are those of the morning of 6 October (section 1 has the committed ones).*

| Change | Effect, with evidence | Commit |
|---|---|---|
| **Budgets from measured p90 plus about 30%** | cheap 6k to 15k, mid 12k to 11k, strong 20k to 24k, judge 20k to 15k (tiers). No row is blocked on its budget in run 2 | `39ee3be`, `1e09d01` |
| **H2 renders the prompt** | `fcrm_prompts.InstructionRenderer`, the evals' renderer promoted; H2 and the evals use one class | `dac2866` |
| **Cheap tier is offered no tool** | the pack is declared complete, so the cheap tier makes one request, not two (screening 5 calls for 5 rows in run 2, against 10 in run 1) | `3e48206` |
| **Compact output schema, and a one-tool round-trip cap** | the output tool's JSON schema is compacted; a refused over-cap call replaces a blocked job | `46e776d`, `39ee3be` |
| **Prompt-cache points per model** | Anthropic models cache the system prompt and tool definitions; run 2 read 150,546 tokens from cache and wrote 8,826. Nova rejects a cache point in the tool list, so it gets none | `8aed1d4` |
| **A retry keeps the tier's whole output allowance, and thinking effort is low** | `max_tokens_factor` is 1.0 on every attempt; effort `low` on the thinking tiers. A live probe measured 12 to 51% fewer output tokens and no reasoning block; on the gate sample medium against low cost the critic 8 to 14k tokens an attempt against 5 to 7k and moved no metric | `756ea06`, `1e09d01` |
| **A refused attempt's tokens are charged to the job** | `AgentDone.usage` holds every attempt | `176358c` |
| **Screening applies R4 itself** | a quality fix that cost 0.68 USD to measure on 100 rows | `176358c` |

Result (run 2 against run 1, same command, same seed):

| | Run 1 | Run 2 |
|---|---|---|
| Gates passing | 1 of 9 | 8 of 9 |
| Rows completed | 7 of 41 | 41 of 41 |
| Agent tokens | 608,534 | 289,550 (down 52%) |
| Agent cost | USD 2.16 | USD 0.62 (down 71%) |
| Agent tokens per completed row | about 86,900 | about 7,060 |
| Judge cost | USD 1.30 over 54 calls | USD 2.53 over 132 calls |
| Total | 730,715 tokens, USD 3.46 | 525,250 tokens, USD 3.14 |

The agents' own cost fell because they stopped being blocked and retried; the judge's rose because it now grades every row instead of the one that completed (evals README). The per-completed-row line is arithmetic from the two columns above it: run 1 mostly bought refusals.

## 5. One case against the 120,000 cap

*Note, 7 October 2026 (ADR-62, ADR-65): measured at `7249d2a` on the 6 October chain (Sonnet 5.5 on mid and strong, an Opus 5.5 critic) and not repeated on the committed chain, whose critic is Nova Pro. The last complete live 21-scene run (18:25 IST on 6 October, commits `e4c14f6` plus `4e156a1`) was on the same chain: 157,345 of the 160,000 enforced cap. Since this section was written H1 enforces the per-case ledger (ADR-50): it reserves one attempt of a job's tier before publishing the job, so the critic reserves 28,000 on the committed chain, and the cap is 160,000 enforced with 120,000 as the target. Point 3 below says the cap is not enforced across jobs; that was true at `7249d2a`.*

`ops/scripts/demo_case_tokens.py` runs every agent once on FCRM-2026-0151 with the real pack builders, the seed-backed tools, the rendered prompts and the shipped budgets, live, and sums what H1 would sum (input plus output). One run, at `7249d2a`, before `research.system` v3:

| | Tokens |
|---|---|
| Eleven jobs, as run (includes one research retry) | **114,626 of 120,000** |
| The same run counting cache reads and writes | 160,350 |
| Every job answering on its first attempt | 91,000 |
| The case's own chain only (intake twice, screening, research, assessment, critic, narrative twice), with the retry / without | 99,600 / 75,900 |
| The three oversight agents inside the 114.6k (examiner 4,575, watchlist 3,615, policy_gap 6,885); they run on demand, not per case | 15,100 |
| Not in the 114.6k: the first document's second intake window (an estimate, not measured) | about 6,000 |

Per job: intake 12,240 and 9,638; screening 4,212; research 11,666 then 23,609 (35,275 with the retry); assessment 14,887; critic 10,917; narrative 5,744 and 6,638; examiner 4,575; watchlist 3,615; policy_gap 6,885 (evals README). The retry was research's first attempt refused by Guard 4 (a clause proposal whose `source_ref` named another clause than its `clause_id`); `research.system` v3 says the two ids are one, and has not been re-measured on the demo case.

Three honest points:

1. **The cap holds with little room.** All eleven jobs with the one retry is 5.4k (4.5%) under 120k, and slightly over it once the second intake window is added. A second retry in the same case would go over. A clean pass is 91.0k.
2. **The original estimates were low.** BUILD-PLAN section 3.4 says "realistic first run about 82k"; the static walkthrough prints 16.9k for its whole run (`docs/requirements/research/06-walkthrough-seed-extraction.md`, section 7: hard-coded `tokens(n)` calls). The measured live figure is 91k to 115k. The 16.9k is a script, not a measurement, and the live demo asserts that tokens stay within 120k, not that they equal it (ADR-27).
3. **The cap is not enforced across jobs.** Each H2 job gets a fresh `CaseBudget`, so H1 sums the case total (and could report it) but does not stop a case at 120k. Guard 3 reserves `tokens x (1 + retries)` per job (strong 48k, judge and cheap 30k, mid 22k); a research and assessment pair would reserve 96k at once, so enforcing it as designed would block the critic (evals README).

## 6. What an eval run costs

*Note, 7 October 2026 (ADR-62, ADR-65): every row below was measured on the 6 October chain (Sonnet 5.5 on mid and strong), with Opus 5.5 as the judge. The only other eval cost on record is a plumbing run of one row per agent on a Sonnet 4.5 chain, which is no longer committed: 7 of 9 gates at about USD 0.44, not a baseline, files not committed. Nothing has been measured on the committed chain. Nova Pro costs far less per token than Opus 5.5 (0.80 and 3.20 against 4.40 and 22.00), and the judge was 80% of run 2, so a `make evals` run on the committed chain is projected at about USD 1; that is not measured, and the totals below do not carry over.*

| Run | Tokens | USD | Source |
|---|---|---|---|
| `make evals` (5 rows per agent, Bedrock judge), run 2 | 525,250 | 3.14 (agents 0.62, judge 2.53) | `evals/results/2026-10-06/index.json` |
| The same, run 1 | 730,715 | 3.46 | `evals/results/2026-10-05/index.json` |
| Diagnostic: budget lifted five times, prompts rendered, stand-in judge | about 1.4M | about 4.7 | evals README |
| Demo case, eleven agent jobs, no judge | 114,626 | about 0.50 | demo sum |
| One agent over its whole dataset, no judge: screening (100 rows) | | 0.59 to 0.68 | evals README |
| One agent over its whole dataset, no judge: watchlist (29 rows) | | 0.11 | evals README |

`make evals` is "about 7 minutes, USD 3" in the evals README, "about USD 4 and 10 minutes" in the Makefile comment, and took 11 minutes in run 1. `make evals-full` runs every row of every dataset and costs several times as much. Use `EVALS_ARGS="--judge fake"` while iterating on a prompt: it makes no judge calls, and every output says FAKE.

## 7. The next optimisations, in order of value

*Note, 7 October 2026 (ADR-62): item 1 was done after this list was written, in the form ADR-50 chose: H1 enforces the per-case ledger and reserves one attempt of a job's tier (not a measured p90), so the critic reserves 28,000 on the committed chain. The other items are as they were, and every figure behind them was measured on the 6 October chain.*

1. **Enforce the per-case ledger across jobs in H1, and fix Guard 3's reservation** (reserve a measured p90, release on completion). Today the 120k cap is a number H1 reports, not a limit it enforces.
2. **Drop empty unknown keys at the decode step.** The model adds a key like `source_ref_note: null` now and then; the contracts forbid extra keys, so one stray key fails the whole answer and the retry costs a full second attempt (research row 0122: a 23.6k retry). The fix was not made so that the committed run is the code that ships.
3. **A judge cache.** A re-grade pays the judge again. The judge was 80% of an eval run on the 6 October chain, with Opus 5.5 as the judge; the share on Nova Pro is not measured.
4. **Cache the cheap tier.** Its prompts are under the 4,096-token Haiku minimum, so no cache point is emitted and screening, intake and watchlist read nothing from cache (tiers, run 2). A longer shared prefix would qualify; whether it pays is unmeasured.
5. **Re-measure the demo case after `research.system` v3**, and measure the second intake window instead of estimating it.
6. **Reconcile the price table with the AWS bill.** The prices are verified against the AWS Price List (6 Oct 2026), not against an invoice; the first month's bill is the check on the rate card (a `global.` inference profile would be about 10% cheaper, at the cost of data residency).
7. **Wire the runtime and build-time reports.** `make token-report` reads Prometheus (needs `make up-obs`) and writes `ops/token-analysis/runtime/`; no report has been committed. `make build-usage` (Claude Code session logs) is not wired.

## How to reproduce

```bash
make evals                                                                    # projected at about USD 1 on the committed chain (not measured); USD 3.14 and 7 to 11 minutes on the 6 October chain; writes evals/results/<date>/
uv run --project src/backend --all-packages python ops/scripts/demo_case_tokens.py   # about USD 0.50 on the 6 October chain (not re-measured); add --only research,assessment for a subset
make up-obs && make token-report                                              # runtime per-agent report from Prometheus
```

Both live commands need AWS credentials in the shell and cost real money. Neither was run by the lane that wrote this page; the numbers above are the committed files'.
