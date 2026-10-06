# TrustOps eval report: live run

Generated 2026-10-06T07:15:25.778261+00:00 from 1 result(s).

Live run `2026-10-06T07-15Z-ac4b7d3` at `ac4b7d3`: every row of every dataset; agents answered on Bedrock via Haiku 4.5, Nova Lite with fully rendered system prompts; graded with a **fake judge** (offline stand-in: `narrative_faithfulness` is a heuristic and the DeepEval metrics did not run). 1/1 gates PASS. Estimated cost $0.62 (366,374 tokens) at the list prices in `ai/models/tiers.yaml` (`placeholder_confirm_before_go_live`). A gate is the agent's primary metric against its threshold, on the sampled rows only; the dashboard's per-metric table shows the rest.

## Agents

| Agent | Dataset | Rows | Producer | Primary metric | Score | Threshold | Result | Other metrics |
|---|---|---|---|---|---|---|---|---|
| screening | screening/fast-lane-100 | 100 | live | fast_lane_precision | 1.000 | >= 0.90 | PASS | fast_lane_recall 0.833 (>= 0.90) FAIL; citation_validity 1.000 (>= 0.95) PASS |

## Usage (estimated at the list prices in `ai/models/tiers.yaml`)

| Agent | Rows | Calls | Retries | Blocked | Failed | Input tok | Output tok | Cost USD (est.) | p50 s | max s | Judge calls | Judge tok | Judge USD (est.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| screening | 100/100 | 104 | 4 | 0 | 0 | 299,693 | 66,681 | 0.6161 | 5.4 | 18.1 | - | - | - |

## Promotion gate (design B §6.4)

| Run | Agent | Primary metric | Baseline | Candidate | Tolerance | Verdict | Reasons |
|---|---|---|---|---|---|---|---|
| screening | screening | fast_lane_precision | n/a | 1.000 | 0.0 | PASS | - |

## Metric detail

| Agent | Producer | Metric | Kind | Score | Num/Den | Rows | Threshold | Source | Result | First flags |
|---|---|---|---|---|---|---|---|---|---|---|
| screening | live | fast_lane_precision (primary) | deterministic | 1.000 | 15/15 | 15 | >= 0.90 | design_b | PASS | - |
| screening | live | fast_lane_recall | deterministic | 0.833 | 15/18 | 18 | >= 0.90 | design_b | FAIL | FCRM-2026-0088: missed; FCRM-2026-0110: missed |
| screening | live | citation_validity | deterministic | 1.000 | 585/585 | 98 | >= 0.95 | design_b | PASS | - |
