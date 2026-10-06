# TrustOps eval report: live run

Generated 2026-10-06T07:15:31.701257+00:00 from 1 result(s).

Live run `2026-10-06T07-15Z-ac4b7d3` at `ac4b7d3`: every row of every dataset; agents answered on Bedrock via Haiku 4.5, Nova Lite with fully rendered system prompts; graded with a **fake judge** (offline stand-in: `narrative_faithfulness` is a heuristic and the DeepEval metrics did not run). 1/1 gates PASS. Estimated cost $0.10 (92,466 tokens) at the list prices in `ai/models/tiers.yaml` (`placeholder_confirm_before_go_live`). A gate is the agent's primary metric against its threshold, on the sampled rows only; the dashboard's per-metric table shows the rest.

## Agents

| Agent | Dataset | Rows | Producer | Primary metric | Score | Threshold | Result | Other metrics |
|---|---|---|---|---|---|---|---|---|
| watchlist | watchlist/reflag-29 | 29 | live | reflag_recall | 1.000 | >= 1.00 | PASS | reflag_precision 0.800 (>= 1.00) FAIL; citation_validity 1.000 (>= 0.95) PASS |

## Usage (estimated at the list prices in `ai/models/tiers.yaml`)

| Agent | Rows | Calls | Retries | Blocked | Failed | Input tok | Output tok | Cost USD (est.) | p50 s | max s | Judge calls | Judge tok | Judge USD (est.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| watchlist | 29/29 | 30 | 1 | 0 | 0 | 90,088 | 2,378 | 0.0981 | 2.0 | 7.3 | - | - | - |

## Promotion gate (design B §6.4)

| Run | Agent | Primary metric | Baseline | Candidate | Tolerance | Verdict | Reasons |
|---|---|---|---|---|---|---|---|
| watchlist | watchlist | reflag_recall | n/a | 1.000 | 0.0 | PASS | - |

## Metric detail

| Agent | Producer | Metric | Kind | Score | Num/Den | Rows | Threshold | Source | Result | First flags |
|---|---|---|---|---|---|---|---|---|---|---|
| watchlist | live | reflag_recall (primary) | deterministic | 1.000 | 4/4 | 3 | >= 1.00 | design_b | PASS | - |
| watchlist | live | reflag_precision | deterministic | 0.800 | 4/5 | 4 | >= 1.00 | proposed | FAIL | invented ('FCRM-2026-0144', 'Algeria') |
| watchlist | live | citation_validity | deterministic | 1.000 | 5/5 | 4 | >= 0.95 | design_b | PASS | - |
