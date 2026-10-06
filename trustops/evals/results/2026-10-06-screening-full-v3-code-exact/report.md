# TrustOps eval report: live run

Generated 2026-10-06T07:11:49.714344+00:00 from 1 result(s).

Live run `2026-10-06T07-11Z-ac4b7d3` at `ac4b7d3`: every row of every dataset; agents answered on Bedrock via Haiku 4.5, Nova Lite with fully rendered system prompts; graded with a **fake judge** (offline stand-in: `narrative_faithfulness` is a heuristic and the DeepEval metrics did not run). 1/1 gates PASS. Estimated cost $0.61 (358,683 tokens) at the list prices in `ai/models/tiers.yaml` (`placeholder_confirm_before_go_live`). A gate is the agent's primary metric against its threshold, on the sampled rows only; the dashboard's per-metric table shows the rest.

## Agents

| Agent | Dataset | Rows | Producer | Primary metric | Score | Threshold | Result | Other metrics |
|---|---|---|---|---|---|---|---|---|
| screening | screening/fast-lane-100 | 100 | live | fast_lane_precision | 1.000 | >= 0.90 | PASS | fast_lane_recall 1.000 (>= 0.90) PASS; citation_validity 1.000 (>= 0.95) PASS |

## Usage (estimated at the list prices in `ai/models/tiers.yaml`)

| Agent | Rows | Calls | Retries | Blocked | Failed | Input tok | Output tok | Cost USD (est.) | p50 s | max s | Judge calls | Judge tok | Judge USD (est.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| screening | 100/100 | 102 | 2 | 0 | 0 | 293,103 | 65,580 | 0.6118 | 5.2 | 9.8 | - | - | - |

## Promotion gate (design B §6.4)

| Run | Agent | Primary metric | Baseline | Candidate | Tolerance | Verdict | Reasons |
|---|---|---|---|---|---|---|---|
| screening | screening | fast_lane_precision | n/a | 1.000 | 0.0 | PASS | - |

## Metric detail

| Agent | Producer | Metric | Kind | Score | Num/Den | Rows | Threshold | Source | Result | First flags |
|---|---|---|---|---|---|---|---|---|---|---|
| screening | live | fast_lane_precision (primary) | deterministic | 1.000 | 18/18 | 18 | >= 0.90 | design_b | PASS | - |
| screening | live | fast_lane_recall | deterministic | 1.000 | 18/18 | 18 | >= 0.90 | design_b | PASS | - |
| screening | live | citation_validity | deterministic | 1.000 | 600/600 | 100 | >= 0.95 | design_b | PASS | - |
