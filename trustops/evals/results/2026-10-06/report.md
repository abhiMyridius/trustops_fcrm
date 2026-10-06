# TrustOps eval report: live run

Generated 2026-10-06T07:28:07.679531+00:00 from 9 result(s).

Live run `2026-10-06T07-28Z-7249d2a` at `7249d2a`: 5 row(s) per agent, seed 7; agents answered on Bedrock via Haiku 4.5, Opus 5.5, Sonnet 5.5 with fully rendered system prompts; graded with the Bedrock judge (Opus 5.5), 3 vote(s) per narrative item. 8/9 gates PASS. Estimated cost $2.86 (525,250 tokens) at the list prices in `ai/models/tiers.yaml` (`placeholder_confirm_before_go_live`). A gate is the agent's primary metric against its threshold, on the sampled rows only; the dashboard's per-metric table shows the rest.

## Agents

| Agent | Dataset | Rows | Producer | Primary metric | Score | Threshold | Result | Other metrics |
|---|---|---|---|---|---|---|---|---|
| screening | screening/fast-lane-100 | 5 | live | fast_lane_precision | 1.000 | >= 0.90 | PASS | fast_lane_recall 1.000 (>= 0.90) PASS; citation_validity 1.000 (>= 0.95) PASS |
| intake | intake/extraction-80 | 5 | live | field_extraction_accuracy | 1.000 | >= 0.90 | PASS | source_span_accuracy 1.000 (>= 0.90) PASS; category_f1 0.667 (>= 0.90) FAIL; gap_question_precision 1.000 (>= 0.90) PASS; citation_validity 1.000 (>= 0.95) PASS |
| research | research/clauses-42 | 5 | live | clause_recall | 1.000 | >= 0.85 | PASS | clause_precision 1.000 (>= 0.70) PASS; precedent_top1 0.000 (>= 0.90) FAIL; citation_validity 1.000 (>= 0.95) PASS |
| assessment | assessment/ratings-80 | 5 | live | li_within_one | 0.700 | >= 0.85 | FAIL | control_match_precision 0.789 (>= 0.90) FAIL; control_match_recall 0.600 (>= 0.90) FAIL; evidence_date_exact 1.000 (>= 0.95) PASS; citation_validity 1.000 (>= 0.95) PASS |
| critic | critic/planted-61 | 5 | live | planted_error_detection | 1.000 | >= 0.85 | PASS | critic_finding_groundedness 1.000 (>= 1.00) PASS; citation_validity 1.000 (>= 0.95) PASS; faithfulness 0.983 (>= 0.90) PASS; answer_relevancy 1.000 (>= 0.80) PASS; hallucination_free 1.000 (>= 0.90) PASS |
| narrative | narrative/recommendation-66 | 5 | live | number_grounding | 1.000 | >= 1.00 | PASS | narrative_restricted_terms 1.000 (>= 1.00) PASS; conditions_traceable 1.000 (>= 1.00) PASS; citation_validity 1.000 (>= 0.95) PASS; narrative_faithfulness 0.200 (>= 0.90) FAIL; faithfulness 1.000 (>= 0.90) PASS; answer_relevancy 1.000 (>= 0.80) PASS; hallucination_free 1.000 (>= 0.90) PASS |
| examiner | examiner/questions-5 | 5 | live | examiner_grounding | 1.000 | >= 1.00 | PASS | must_cite_coverage 0.889 (>= 0.90) FAIL; refusal_rate 1.000 (>= 0.95) PASS; faithfulness 1.000 (>= 0.90) PASS; answer_relevancy 1.000 (>= 0.80) PASS; hallucination_free 1.000 (>= 0.90) PASS |
| watchlist | watchlist/reflag-29 | 5 | live | reflag_recall | 1.000 | >= 1.00 | PASS | reflag_precision 1.000 (>= 1.00) PASS; citation_validity 1.000 (>= 0.95) PASS |
| policy_gap | policy_gap/suggestions-1 | 1 | live | gap_targets_recorded_gap | 1.000 | >= 1.00 | PASS | suggestion_novelty 1.000 (>= 1.00) PASS; citation_validity 1.000 (>= 0.95) PASS |

## Usage (estimated at the list prices in `ai/models/tiers.yaml`)

| Agent | Rows | Calls | Retries | Blocked | Failed | Input tok | Output tok | Cost USD (est.) | p50 s | max s | Judge calls | Judge tok | Judge USD (est.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| screening | 5/100 | 5 | 0 | 0 | 0 | 14,507 | 3,294 | 0.0310 | 5.2 | 6.1 | - | - | - |
| intake | 5/80 | 5 | 0 | 0 | 0 | 23,898 | 4,841 | 0.0481 | 6.4 | 9.6 | - | - | - |
| research | 5/42 | 10 | 1 | 0 | 0 | 80,687 | 5,710 | 0.1336 | 8.6 | 22.4 | - | - | - |
| assessment | 5/80 | 6 | 0 | 0 | 0 | 42,658 | 10,947 | 0.1318 | 15.7 | 22.8 | - | - | - |
| critic | 5/61 | 5 | 0 | 0 | 0 | 26,185 | 4,055 | 0.1084 | 10.8 | 15.2 | 45 | 87,312 | 0.8084 |
| narrative | 5/66 | 5 | 0 | 0 | 0 | 22,278 | 2,503 | 0.0322 | 5.9 | 7.0 | 60 | 81,886 | 0.8570 |
| examiner | 5/5 | 6 | 0 | 0 | 0 | 23,672 | 1,841 | 0.0519 | 4.5 | 7.3 | 27 | 66,502 | 0.6304 |
| watchlist | 5/29 | 5 | 0 | 0 | 0 | 15,099 | 849 | 0.0193 | 2.9 | 3.3 | - | - | - |
| policy_gap | 1/1 | 1 | 0 | 0 | 0 | 6,037 | 489 | 0.0069 | 4.5 | 4.5 | - | - | - |

## Promotion gate (design B §6.4)

| Run | Agent | Primary metric | Baseline | Candidate | Tolerance | Verdict | Reasons |
|---|---|---|---|---|---|---|---|
| screening | screening | fast_lane_precision | n/a | 1.000 | 0.0 | PASS | - |
| intake | intake | field_extraction_accuracy | 0.000 | 1.000 | 0.0 | PASS | - |
| research | research | clause_recall | 0.000 | 1.000 | 0.0 | PASS | - |
| assessment | assessment | li_within_one | 0.000 | 0.700 | 0.0 | FAIL | li_within_one 0.700 is below its threshold 0.85 |
| critic | critic | planted_error_detection | 0.000 | 1.000 | 0.0 | PASS | - |
| narrative | narrative | number_grounding | 1.000 | 1.000 | 0.0 | PASS | - |
| examiner | examiner | examiner_grounding | 1.000 | 1.000 | 0.0 | PASS | - |
| watchlist | watchlist | reflag_recall | 0.000 | 1.000 | 0.0 | PASS | - |
| policy_gap | policy_gap | gap_targets_recorded_gap | n/a | 1.000 | 0.0 | PASS | - |

## Metric detail

| Agent | Producer | Metric | Kind | Score | Num/Den | Rows | Threshold | Source | Result | First flags |
|---|---|---|---|---|---|---|---|---|---|---|
| screening | live | fast_lane_precision (primary) | deterministic | 1.000 | 2/2 | 2 | >= 0.90 | design_b | PASS | - |
| screening | live | fast_lane_recall | deterministic | 1.000 | 2/2 | 2 | >= 0.90 | design_b | PASS | - |
| screening | live | citation_validity | deterministic | 1.000 | 30/30 | 5 | >= 0.95 | design_b | PASS | - |
| intake | live | field_extraction_accuracy (primary) | deterministic | 1.000 | 9/9 | 1 | >= 0.90 | design_b | PASS | - |
| intake | live | source_span_accuracy | deterministic | 1.000 | 9/9 | 1 | >= 0.90 | design_b | PASS | - |
| intake | live | category_f1 | deterministic | 0.667 | 10/15 | 5 | >= 0.90 | design_b | FAIL | fn ['new_product']; fn ['new_feature', 'process_change'] |
| intake | live | gap_question_precision | deterministic | 1.000 | 4/4 | 1 | >= 0.90 | design_b | PASS | - |
| intake | live | citation_validity | deterministic | 1.000 | 14/14 | 3 | >= 0.95 | design_b | PASS | - |
| research | live | clause_recall (primary) | deterministic | 1.000 | 14/14 | 5 | >= 0.85 | design_b | PASS | - |
| research | live | clause_precision | deterministic | 1.000 | 14/14 | 5 | >= 0.70 | design_b | PASS | - |
| research | live | precedent_top1 | deterministic | 0.000 | 0/1 | 1 | >= 0.90 | proposed | FAIL | top None |
| research | live | citation_validity | deterministic | 1.000 | 14/14 | 5 | >= 0.95 | design_b | PASS | - |
| assessment | live | li_within_one (primary) | deterministic | 0.700 | 7/10 | 5 | >= 0.85 | design_b | FAIL | geography: 3x4 vs 4x2; customer: 2x3 vs 3x5 |
| assessment | live | control_match_precision | deterministic | 0.789 | 30/38 | 4 | >= 0.90 | proposed | FAIL | extra ('product', 'CTL-P01'); extra ('product', 'CTL-P06') |
| assessment | live | control_match_recall | deterministic | 0.600 | 30/50 | 5 | >= 0.90 | design_b | FAIL | missed ('customer', 'CTL-C03'); missed ('customer', 'CTL-C05') |
| assessment | live | evidence_date_exact | deterministic | 1.000 | 30/30 | 4 | >= 0.95 | proposed | PASS | - |
| assessment | live | citation_validity | deterministic | 1.000 | 48/48 | 5 | >= 0.95 | design_b | PASS | - |
| critic | live | planted_error_detection (primary) | deterministic | 1.000 | 6/6 | 5 | >= 0.85 | design_b | PASS | - |
| critic | live | critic_finding_groundedness | deterministic | 1.000 | 10/10 | 5 | >= 1.00 | proposed | PASS | - |
| critic | live | citation_validity | deterministic | 1.000 | 10/10 | 5 | >= 0.95 | design_b | PASS | - |
| critic | live | faithfulness | llm_judge | 0.983 | 4.91304/5 | 5 | >= 0.90 | proposed | PASS | - |
| critic | live | answer_relevancy | llm_judge | 1.000 | 5/5 | 5 | >= 0.80 | proposed | PASS | - |
| critic | live | hallucination_free | llm_judge | 1.000 | 5/5 | 5 | >= 0.90 | proposed | PASS | - |
| narrative | live | number_grounding (primary) | deterministic | 1.000 | 10/10 | 5 | >= 1.00 | design_b | PASS | - |
| narrative | live | narrative_restricted_terms | deterministic | 1.000 | 5/5 | 5 | >= 1.00 | design_b | PASS | - |
| narrative | live | conditions_traceable | deterministic | 1.000 | 3/3 | 1 | >= 1.00 | design_b | PASS | - |
| narrative | live | citation_validity | deterministic | 1.000 | 5/5 | 5 | >= 0.95 | design_b | PASS | - |
| narrative | live | narrative_faithfulness | llm_judge | 0.200 | 1/5 | 5 | >= 0.90 | design_b | FAIL | judge 0.67 < 0.8; judge 0.34 < 0.8 |
| narrative | live | faithfulness | llm_judge | 1.000 | 5/5 | 5 | >= 0.90 | proposed | PASS | - |
| narrative | live | answer_relevancy | llm_judge | 1.000 | 5/5 | 5 | >= 0.80 | proposed | PASS | - |
| narrative | live | hallucination_free | llm_judge | 1.000 | 5/5 | 5 | >= 0.90 | proposed | PASS | - |
| examiner | live | examiner_grounding (primary) | deterministic | 1.000 | 14/14 | 3 | >= 1.00 | design_b | PASS | - |
| examiner | live | must_cite_coverage | deterministic | 0.889 | 8/9 | 3 | >= 0.90 | proposed | FAIL | no vote.revealed |
| examiner | live | refusal_rate | deterministic | 1.000 | 2/2 | 2 | >= 0.95 | design_b | PASS | - |
| examiner | live | faithfulness | llm_judge | 1.000 | 3/3 | 3 | >= 0.90 | proposed | PASS | - |
| examiner | live | answer_relevancy | llm_judge | 1.000 | 3/3 | 3 | >= 0.80 | proposed | PASS | - |
| examiner | live | hallucination_free | llm_judge | 1.000 | 3/3 | 3 | >= 0.90 | proposed | PASS | - |
| watchlist | live | reflag_recall (primary) | deterministic | 1.000 | 4/4 | 3 | >= 1.00 | design_b | PASS | - |
| watchlist | live | reflag_precision | deterministic | 1.000 | 4/4 | 3 | >= 1.00 | proposed | PASS | - |
| watchlist | live | citation_validity | deterministic | 1.000 | 4/4 | 3 | >= 0.95 | design_b | PASS | - |
| policy_gap | live | gap_targets_recorded_gap (primary) | deterministic | 1.000 | 1/1 | 1 | >= 1.00 | proposed | PASS | - |
| policy_gap | live | suggestion_novelty | deterministic | 1.000 | 1/1 | 1 | >= 1.00 | proposed | PASS | - |
| policy_gap | live | citation_validity | deterministic | 1.000 | 1/1 | 1 | >= 0.95 | design_b | PASS | - |
