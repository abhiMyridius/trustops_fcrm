# TrustOps eval report: live run

Generated 2026-10-05T17:46:55.136667+00:00 from 9 result(s).

Live run `2026-10-05T17-46Z-4c715bb` at `4c715bb`: 5 row(s) per agent, seed 7; agents answered on Bedrock via Haiku 4.5, Nova Lite, Opus 5.5, Sonnet 5.5 with fully rendered system prompts; graded with a **fake judge** (offline stand-in: `narrative_faithfulness` is a heuristic and the DeepEval metrics did not run). 2/9 gates PASS. Estimated cost $4.26 (1,395,420 tokens) at the list prices in `ai/models/tiers.yaml` (`placeholder_confirm_before_go_live`). A gate is the agent's primary metric against its threshold, on the sampled rows only; the dashboard's per-metric table shows the rest. DIAGNOSTIC: the per-attempt token budgets were lifted x5; this is not the system as shipped.

## Agents

| Agent | Dataset | Rows | Producer | Primary metric | Score | Threshold | Result | Other metrics |
|---|---|---|---|---|---|---|---|---|
| screening | screening/fast-lane-100 | 5 | live | fast_lane_precision | n/a | >= 0.90 | n/a | fast_lane_recall 0.000 (>= 0.90) FAIL; citation_validity n/a (>= 0.95) n/a |
| intake | intake/extraction-80 | 5 | live | field_extraction_accuracy | 1.000 | >= 0.90 | PASS | source_span_accuracy 1.000 (>= 0.90) PASS; category_f1 0.000 (>= 0.90) FAIL; gap_question_precision 1.000 (>= 0.90) PASS; citation_validity 1.000 (>= 0.95) PASS |
| research | research/clauses-42 | 5 | live | clause_recall | 0.000 | >= 0.85 | FAIL | clause_precision n/a (>= 0.70) n/a; precedent_top1 0.000 (>= 0.90) FAIL; citation_validity n/a (>= 0.95) n/a |
| assessment | assessment/ratings-80 | 5 | live | li_within_one | 0.200 | >= 0.85 | FAIL | control_match_precision 0.600 (>= 0.90) FAIL; control_match_recall 0.060 (>= 0.90) FAIL; evidence_date_exact 1.000 (>= 0.95) PASS; citation_validity 1.000 (>= 0.95) PASS |
| critic | critic/planted-61 | 5 | live | planted_error_detection | 0.000 | >= 0.85 | FAIL | critic_finding_groundedness n/a (>= 1.00) n/a; citation_validity n/a (>= 0.95) n/a |
| narrative | narrative/recommendation-66 | 5 | live | number_grounding | n/a | >= 1.00 | n/a | narrative_restricted_terms n/a (>= 1.00) n/a; conditions_traceable n/a (>= 1.00) n/a; citation_validity n/a (>= 0.95) n/a; narrative_faithfulness n/a (>= 0.90) n/a |
| examiner | examiner/questions-5 | 5 | live | examiner_grounding | 1.000 | >= 1.00 | PASS | must_cite_coverage 0.889 (>= 0.90) FAIL; refusal_rate 1.000 (>= 0.95) PASS |
| watchlist | watchlist/reflag-29 | 5 | live | reflag_recall | 0.000 | >= 1.00 | FAIL | reflag_precision n/a (>= 1.00) n/a; citation_validity n/a (>= 0.95) n/a |
| policy_gap | policy_gap/suggestions-1 | 1 | live | gap_targets_recorded_gap | n/a | >= 1.00 | n/a | suggestion_novelty n/a (>= 1.00) n/a; citation_validity n/a (>= 0.95) n/a |

## Usage (estimated at the list prices in `ai/models/tiers.yaml`)

| Agent | Rows | Calls | Retries | Blocked | Failed | Input tok | Output tok | Cost USD (est.) | p50 s | max s | Judge calls | Judge tok | Judge USD (est.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| screening | 5/100 | 20 | 5 | 5 | 0 | 83,841 | 4,025 | 0.0657 | 11.3 | 12.7 | - | - | - |
| intake | 5/80 | 14 | 0 | 0 | 0 | 102,760 | 4,910 | 0.1273 | 9.4 | 18.7 | - | - | - |
| research | 5/42 | 33 | 5 | 5 | 0 | 441,330 | 33,383 | 1.8324 | 107.8 | 148.4 | - | - | - |
| assessment | 5/80 | 19 | 3 | 3 | 0 | 212,034 | 33,857 | 1.0199 | 114.4 | 217.6 | - | - | - |
| critic | 5/61 | 19 | 5 | 5 | 0 | 134,744 | 15,368 | 0.6372 | 39.8 | 135.3 | - | - | - |
| narrative | 5/66 | 24 | 5 | 5 | 0 | 142,915 | 9,205 | 0.2901 | 20.7 | 31.7 | - | - | - |
| examiner | 5/5 | 7 | 0 | 0 | 0 | 40,629 | 4,422 | 0.1255 | 8.5 | 12.3 | - | - | - |
| watchlist | 5/29 | 18 | 2 | 5 | 0 | 73,155 | 1,451 | 0.0590 | 5.1 | 11.2 | - | - | - |
| policy_gap | 1/1 | 7 | 1 | 1 | 0 | 54,388 | 3,003 | 0.1061 | 29.1 | 29.1 | - | - | - |

## Promotion gate (design B §6.4)

| Run | Agent | Primary metric | Baseline | Candidate | Tolerance | Verdict | Reasons |
|---|---|---|---|---|---|---|---|
| screening | screening | fast_lane_precision | n/a | n/a | 0.0 | FAIL | fast_lane_precision was not measured on the candidate; completed 0/5 rows (0%); a gate needs at least 80%, because a blocked or failed row has no output to measure |
| intake | intake | field_extraction_accuracy | n/a | 1.000 | 0.0 | PASS | - |
| research | research | clause_recall | n/a | 0.000 | 0.0 | FAIL | clause_recall 0.000 is below its threshold 0.85; completed 0/5 rows (0%); a gate needs at least 80%, because a blocked or failed row has no output to measure |
| assessment | assessment | li_within_one | n/a | 0.200 | 0.0 | FAIL | li_within_one 0.200 is below its threshold 0.85; completed 2/5 rows (40%); a gate needs at least 80%, because a blocked or failed row has no output to measure |
| critic | critic | planted_error_detection | n/a | 0.000 | 0.0 | FAIL | planted_error_detection 0.000 is below its threshold 0.85; completed 0/5 rows (0%); a gate needs at least 80%, because a blocked or failed row has no output to measure |
| narrative | narrative | number_grounding | n/a | n/a | 0.0 | FAIL | number_grounding was not measured on the candidate; completed 0/5 rows (0%); a gate needs at least 80%, because a blocked or failed row has no output to measure |
| examiner | examiner | examiner_grounding | n/a | 1.000 | 0.0 | PASS | - |
| watchlist | watchlist | reflag_recall | n/a | 0.000 | 0.0 | FAIL | reflag_recall 0.000 is below its threshold 1.00; completed 0/5 rows (0%); a gate needs at least 80%, because a blocked or failed row has no output to measure |
| policy_gap | policy_gap | gap_targets_recorded_gap | n/a | n/a | 0.0 | FAIL | gap_targets_recorded_gap was not measured on the candidate; completed 0/1 rows (0%); a gate needs at least 80%, because a blocked or failed row has no output to measure |

## Metric detail

| Agent | Producer | Metric | Kind | Score | Num/Den | Rows | Threshold | Source | Result | First flags |
|---|---|---|---|---|---|---|---|---|---|---|
| screening | live | fast_lane_precision (primary) | deterministic | n/a | 0/0 | 0 | >= 0.90 | design_b | n/a | - |
| screening | live | fast_lane_recall | deterministic | 0.000 | 0/2 | 2 | >= 0.90 | design_b | FAIL | FCRM-2026-0089~vol-4.2M: missed; FCRM-2026-0106~vol-4.2M: missed |
| screening | live | citation_validity | deterministic | n/a | 0/0 | 0 | >= 0.95 | design_b | n/a | - |
| intake | live | field_extraction_accuracy (primary) | deterministic | 1.000 | 9/9 | 1 | >= 0.90 | design_b | PASS | - |
| intake | live | source_span_accuracy | deterministic | 1.000 | 9/9 | 1 | >= 0.90 | design_b | PASS | - |
| intake | live | category_f1 | deterministic | 0.000 | 0/10 | 5 | >= 0.90 | design_b | FAIL | fn ['new_geography', 'new_product', 'vendor_onboarding']; fn ['new_feature', 'process_change'] |
| intake | live | gap_question_precision | deterministic | 1.000 | 9/9 | 1 | >= 0.90 | design_b | PASS | - |
| intake | live | citation_validity | deterministic | 1.000 | 9/9 | 1 | >= 0.95 | design_b | PASS | - |
| research | live | clause_recall (primary) | deterministic | 0.000 | 0/14 | 5 | >= 0.85 | design_b | FAIL | missed FFIEC-TPPP-04; missed FCRM42-3.1 |
| research | live | clause_precision | deterministic | n/a | 0/0 | 0 | >= 0.70 | design_b | n/a | - |
| research | live | precedent_top1 | deterministic | 0.000 | 0/1 | 1 | >= 0.90 | proposed | FAIL | top None |
| research | live | citation_validity | deterministic | n/a | 0/0 | 0 | >= 0.95 | design_b | n/a | - |
| assessment | live | li_within_one (primary) | deterministic | 0.200 | 2/10 | 5 | >= 0.85 | design_b | FAIL | customer: missing vs 4x4; product: missing vs 5x3 |
| assessment | live | control_match_precision | deterministic | 0.600 | 3/5 | 2 | >= 0.90 | proposed | FAIL | extra ('product', 'CTL-P02'); extra ('product', 'CTL-P03') |
| assessment | live | control_match_recall | deterministic | 0.060 | 3/50 | 5 | >= 0.90 | design_b | FAIL | missed ('customer', 'CTL-C01'); missed ('customer', 'CTL-C02') |
| assessment | live | evidence_date_exact | deterministic | 1.000 | 3/3 | 1 | >= 0.95 | proposed | PASS | - |
| assessment | live | citation_validity | deterministic | 1.000 | 8/8 | 2 | >= 0.95 | design_b | PASS | - |
| critic | live | planted_error_detection (primary) | deterministic | 0.000 | 0/6 | 5 | >= 0.85 | design_b | FAIL | missed precedent_deviation on CTL-T01; missed evidence_recency on CTL-T01 |
| critic | live | critic_finding_groundedness | deterministic | n/a | 0/0 | 0 | >= 1.00 | proposed | n/a | - |
| critic | live | citation_validity | deterministic | n/a | 0/0 | 0 | >= 0.95 | design_b | n/a | - |
| narrative | live | number_grounding (primary) | deterministic | n/a | 0/0 | 0 | >= 1.00 | design_b | n/a | - |
| narrative | live | narrative_restricted_terms | deterministic | n/a | 0/0 | 0 | >= 1.00 | design_b | n/a | - |
| narrative | live | conditions_traceable | deterministic | n/a | 0/0 | 0 | >= 1.00 | design_b | n/a | - |
| narrative | live | citation_validity | deterministic | n/a | 0/0 | 0 | >= 0.95 | design_b | n/a | - |
| narrative | live | narrative_faithfulness | llm_judge | n/a | 0/0 | 0 | >= 0.90 | design_b | n/a | - |
| examiner | live | examiner_grounding (primary) | deterministic | 1.000 | 20/20 | 3 | >= 1.00 | design_b | PASS | - |
| examiner | live | must_cite_coverage | deterministic | 0.889 | 8/9 | 3 | >= 0.90 | proposed | FAIL | no vote.revealed |
| examiner | live | refusal_rate | deterministic | 1.000 | 2/2 | 2 | >= 0.95 | design_b | PASS | - |
| watchlist | live | reflag_recall (primary) | deterministic | 0.000 | 0/4 | 3 | >= 1.00 | design_b | FAIL | missed ('FCRM-2026-0144', 'Algeria'); missed ('FCRM-2026-0147', 'Namibia') |
| watchlist | live | reflag_precision | deterministic | n/a | 0/0 | 0 | >= 1.00 | proposed | n/a | - |
| watchlist | live | citation_validity | deterministic | n/a | 0/0 | 0 | >= 0.95 | design_b | n/a | - |
| policy_gap | live | gap_targets_recorded_gap (primary) | deterministic | n/a | 0/0 | 0 | >= 1.00 | proposed | n/a | - |
| policy_gap | live | suggestion_novelty | deterministic | n/a | 0/0 | 0 | >= 1.00 | proposed | n/a | - |
| policy_gap | live | citation_validity | deterministic | n/a | 0/0 | 0 | >= 0.95 | design_b | n/a | - |
