# TrustOps eval report: live run

Generated 2026-10-06T07:05:46.005791+00:00 from 1 result(s).

Live run `2026-10-06T07-05Z-ac4b7d3` at `ac4b7d3`: every row of every dataset; agents answered on Bedrock via Haiku 4.5, Nova Lite with fully rendered system prompts; graded with a **fake judge** (offline stand-in: `narrative_faithfulness` is a heuristic and the DeepEval metrics did not run). 0/1 gates PASS. Estimated cost $0.54 (346,666 tokens) at the list prices in `ai/models/tiers.yaml` (`placeholder_confirm_before_go_live`). A gate is the agent's primary metric against its threshold, on the sampled rows only; the dashboard's per-metric table shows the rest.

## Agents

| Agent | Dataset | Rows | Producer | Primary metric | Score | Threshold | Result | Other metrics |
|---|---|---|---|---|---|---|---|---|
| screening | screening/fast-lane-100 | 100 | live | fast_lane_precision | 0.583 | >= 0.90 | FAIL | fast_lane_recall 0.389 (>= 0.90) FAIL; citation_validity 1.000 (>= 0.95) PASS |

## Usage (estimated at the list prices in `ai/models/tiers.yaml`)

| Agent | Rows | Calls | Retries | Blocked | Failed | Input tok | Output tok | Cost USD (est.) | p50 s | max s | Judge calls | Judge tok | Judge USD (est.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| screening | 100/100 | 110 | 10 | 0 | 0 | 288,657 | 58,009 | 0.5383 | 5.1 | 10.7 | - | - | - |

## Promotion gate (design B §6.4)

| Run | Agent | Primary metric | Baseline | Candidate | Tolerance | Verdict | Reasons |
|---|---|---|---|---|---|---|---|
| screening | screening | fast_lane_precision | n/a | 0.583 | 0.0 | FAIL | fast_lane_precision 0.583 is below its threshold 0.90 |

## Metric detail

| Agent | Producer | Metric | Kind | Score | Num/Den | Rows | Threshold | Source | Result | First flags |
|---|---|---|---|---|---|---|---|---|---|---|
| screening | live | fast_lane_precision (primary) | deterministic | 0.583 | 7/12 | 12 | >= 0.90 | design_b | FAIL | FCRM-2026-0089~vol-5.0M: false positive; FCRM-2026-0106~vol-5.0M: false positive |
| screening | live | fast_lane_recall | deterministic | 0.389 | 7/18 | 18 | >= 0.90 | design_b | FAIL | FCRM-2026-0088: missed; FCRM-2026-0091: missed |
| screening | live | citation_validity | deterministic | 1.000 | 495/495 | 99 | >= 0.95 | design_b | PASS | - |
