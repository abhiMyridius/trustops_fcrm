# TrustOps eval report: live run

Generated 2026-10-05T17:31:37.794035+00:00 from 9 result(s).

Live run `2026-10-05T17-31Z-4c715bb` at `4c715bb`: 5 row(s) per agent, seed 7; agents answered on Bedrock via Haiku 4.5, Nova Lite, Opus 5.5, Sonnet 5.5 with the system prompts as the H2 worker sends them (unrendered template bodies); graded with the Bedrock judge (Opus 5.5), 3 vote(s) per narrative item. 1/9 gates PASS. Estimated cost $3.14 (730,715 tokens) at the list prices in `ai/models/tiers.yaml` (`placeholder_confirm_before_go_live`). A gate is the agent's primary metric against its threshold, on the sampled rows only; the dashboard's per-metric table shows the rest.

## Agents

| Agent | Dataset | Rows | Producer | Primary metric | Score | Threshold | Result | Other metrics |
|---|---|---|---|---|---|---|---|---|
| screening | screening/fast-lane-100 | 5 | live | fast_lane_precision | n/a | >= 0.90 | n/a | fast_lane_recall 0.000 (>= 0.90) FAIL; citation_validity n/a (>= 0.95) n/a |
| intake | intake/extraction-80 | 5 | live | field_extraction_accuracy | 0.000 | >= 0.90 | FAIL | source_span_accuracy n/a (>= 0.90) n/a; category_f1 0.000 (>= 0.90) FAIL; gap_question_precision n/a (>= 0.90) n/a; citation_validity n/a (>= 0.95) n/a |
| research | research/clauses-42 | 5 | live | clause_recall | 0.000 | >= 0.85 | FAIL | clause_precision n/a (>= 0.70) n/a; precedent_top1 0.000 (>= 0.90) FAIL; citation_validity n/a (>= 0.95) n/a |
| assessment | assessment/ratings-80 | 5 | live | li_within_one | 0.000 | >= 0.85 | FAIL | control_match_precision 0.000 (>= 0.90) FAIL; control_match_recall 0.000 (>= 0.90) FAIL; evidence_date_exact n/a (>= 0.95) n/a; citation_validity 1.000 (>= 0.95) PASS |
| critic | critic/planted-61 | 5 | live | planted_error_detection | 0.000 | >= 0.85 | FAIL | critic_finding_groundedness n/a (>= 1.00) n/a; citation_validity n/a (>= 0.95) n/a; faithfulness n/a (>= 0.90) n/a; answer_relevancy n/a (>= 0.80) n/a; hallucination_free n/a (>= 0.90) n/a |
| narrative | narrative/recommendation-66 | 5 | live | number_grounding | 1.000 | >= 1.00 | PASS | narrative_restricted_terms 1.000 (>= 1.00) PASS; conditions_traceable n/a (>= 1.00) n/a; citation_validity n/a (>= 0.95) n/a; narrative_faithfulness n/a (>= 0.90) n/a; faithfulness 1.000 (>= 0.90) PASS; answer_relevancy 1.000 (>= 0.80) PASS; hallucination_free 0.750 (>= 0.90) FAIL |
| examiner | examiner/questions-5 | 5 | live | examiner_grounding | 1.000 | >= 1.00 | PASS | must_cite_coverage 0.889 (>= 0.90) FAIL; refusal_rate 0.000 (>= 0.95) FAIL; faithfulness 1.000 (>= 0.90) PASS; answer_relevancy 1.000 (>= 0.80) PASS; hallucination_free 1.000 (>= 0.90) PASS |
| watchlist | watchlist/reflag-29 | 5 | live | reflag_recall | 0.000 | >= 1.00 | FAIL | reflag_precision n/a (>= 1.00) n/a; citation_validity n/a (>= 0.95) n/a |
| policy_gap | policy_gap/suggestions-1 | 1 | live | gap_targets_recorded_gap | n/a | >= 1.00 | n/a | suggestion_novelty n/a (>= 1.00) n/a; citation_validity n/a (>= 0.95) n/a |

## Usage (estimated at the list prices in `ai/models/tiers.yaml`)

| Agent | Rows | Calls | Retries | Blocked | Failed | Input tok | Output tok | Cost USD (est.) | p50 s | max s | Judge calls | Judge tok | Judge USD (est.) |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| screening | 5/100 | 10 | 0 | 5 | 0 | 30,601 | 3,668 | 0.0489 | 8.7 | 12.0 | - | - | - |
| intake | 5/80 | 10 | 0 | 5 | 0 | 41,499 | 2,885 | 0.0559 | 5.2 | 12.3 | - | - | - |
| research | 5/42 | 15 | 0 | 5 | 0 | 103,862 | 16,210 | 0.3698 | 27.9 | 28.3 | - | - | - |
| assessment | 5/80 | 16 | 2 | 4 | 0 | 102,487 | 28,956 | 0.5850 | 46.4 | 106.5 | - | - | - |
| critic | 5/61 | 19 | 5 | 5 | 0 | 98,629 | 17,059 | 0.5432 | 53.9 | 80.2 | - | - | - |
| narrative | 5/66 | 21 | 5 | 4 | 0 | 80,867 | 7,897 | 0.1912 | 20.3 | 44.7 | 9 | 9,085 | 0.0903 |
| examiner | 5/5 | 7 | 0 | 0 | 0 | 28,818 | 6,097 | 0.1186 | 10.6 | 14.8 | 45 | 113,096 | 1.0933 |
| watchlist | 5/29 | 13 | 1 | 5 | 0 | 32,847 | 1,330 | 0.0324 | 7.9 | 10.0 | - | - | - |
| policy_gap | 1/1 | 1 | 0 | 1 | 0 | 4,120 | 702 | 0.0153 | 7.2 | 7.2 | - | - | - |

## Promotion gate (design B §6.4)

| Run | Agent | Primary metric | Baseline | Candidate | Tolerance | Verdict | Reasons |
|---|---|---|---|---|---|---|---|
| screening | screening | fast_lane_precision | n/a | n/a | 0.0 | FAIL | fast_lane_precision was not measured on the candidate; completed 0/5 rows (0%); a gate needs at least 80%, because a blocked or failed row has no output to measure |
| intake | intake | field_extraction_accuracy | n/a | 0.000 | 0.0 | FAIL | field_extraction_accuracy 0.000 is below its threshold 0.90; completed 0/5 rows (0%); a gate needs at least 80%, because a blocked or failed row has no output to measure |
| research | research | clause_recall | n/a | 0.000 | 0.0 | FAIL | clause_recall 0.000 is below its threshold 0.85; completed 0/5 rows (0%); a gate needs at least 80%, because a blocked or failed row has no output to measure |
| assessment | assessment | li_within_one | n/a | 0.000 | 0.0 | FAIL | li_within_one 0.000 is below its threshold 0.85; completed 1/5 rows (20%); a gate needs at least 80%, because a blocked or failed row has no output to measure |
| critic | critic | planted_error_detection | n/a | 0.000 | 0.0 | FAIL | planted_error_detection 0.000 is below its threshold 0.85; completed 0/5 rows (0%); a gate needs at least 80%, because a blocked or failed row has no output to measure |
| narrative | narrative | number_grounding | n/a | 1.000 | 0.0 | FAIL | completed 1/5 rows (20%); a gate needs at least 80%, because a blocked or failed row has no output to measure |
| examiner | examiner | examiner_grounding | n/a | 1.000 | 0.0 | PASS | - |
| watchlist | watchlist | reflag_recall | n/a | 0.000 | 0.0 | FAIL | reflag_recall 0.000 is below its threshold 1.00; completed 0/5 rows (0%); a gate needs at least 80%, because a blocked or failed row has no output to measure |
| policy_gap | policy_gap | gap_targets_recorded_gap | n/a | n/a | 0.0 | FAIL | gap_targets_recorded_gap was not measured on the candidate; completed 0/1 rows (0%); a gate needs at least 80%, because a blocked or failed row has no output to measure |

## Metric detail

| Agent | Producer | Metric | Kind | Score | Num/Den | Rows | Threshold | Source | Result | First flags |
|---|---|---|---|---|---|---|---|---|---|---|
| screening | live | fast_lane_precision (primary) | deterministic | n/a | 0/0 | 0 | >= 0.90 | design_b | n/a | - |
| screening | live | fast_lane_recall | deterministic | 0.000 | 0/2 | 2 | >= 0.90 | design_b | FAIL | FCRM-2026-0089~vol-4.2M: missed; FCRM-2026-0106~vol-4.2M: missed |
| screening | live | citation_validity | deterministic | n/a | 0/0 | 0 | >= 0.95 | design_b | n/a | - |
| intake | live | field_extraction_accuracy (primary) | deterministic | 0.000 | 0/9 | 1 | >= 0.90 | design_b | FAIL | F01: 'missing'; F02: 'missing' |
| intake | live | source_span_accuracy | deterministic | n/a | 0/0 | 0 | >= 0.90 | design_b | n/a | - |
| intake | live | category_f1 | deterministic | 0.000 | 0/10 | 5 | >= 0.90 | design_b | FAIL | fn ['new_geography', 'new_product', 'vendor_onboarding']; fn ['new_feature', 'process_change'] |
| intake | live | gap_question_precision | deterministic | n/a | 0/0 | 0 | >= 0.90 | design_b | n/a | - |
| intake | live | citation_validity | deterministic | n/a | 0/0 | 0 | >= 0.95 | design_b | n/a | - |
| research | live | clause_recall (primary) | deterministic | 0.000 | 0/14 | 5 | >= 0.85 | design_b | FAIL | missed FFIEC-TPPP-04; missed FCRM42-3.1 |
| research | live | clause_precision | deterministic | n/a | 0/0 | 0 | >= 0.70 | design_b | n/a | - |
| research | live | precedent_top1 | deterministic | 0.000 | 0/1 | 1 | >= 0.90 | proposed | FAIL | top None |
| research | live | citation_validity | deterministic | n/a | 0/0 | 0 | >= 0.95 | design_b | n/a | - |
| assessment | live | li_within_one (primary) | deterministic | 0.000 | 0/10 | 5 | >= 0.85 | design_b | FAIL | customer: missing vs 4x4; product: missing vs 5x3 |
| assessment | live | control_match_precision | deterministic | 0.000 | 0/1 | 1 | >= 0.90 | proposed | FAIL | extra ('product', 'CTL-P02') |
| assessment | live | control_match_recall | deterministic | 0.000 | 0/50 | 5 | >= 0.90 | design_b | FAIL | missed ('customer', 'CTL-C01'); missed ('customer', 'CTL-C02') |
| assessment | live | evidence_date_exact | deterministic | n/a | 0/0 | 0 | >= 0.95 | proposed | n/a | - |
| assessment | live | citation_validity | deterministic | 1.000 | 1/1 | 1 | >= 0.95 | design_b | PASS | - |
| critic | live | planted_error_detection (primary) | deterministic | 0.000 | 0/6 | 5 | >= 0.85 | design_b | FAIL | missed precedent_deviation on CTL-T01; missed evidence_recency on CTL-T01 |
| critic | live | critic_finding_groundedness | deterministic | n/a | 0/0 | 0 | >= 1.00 | proposed | n/a | - |
| critic | live | citation_validity | deterministic | n/a | 0/0 | 0 | >= 0.95 | design_b | n/a | - |
| critic | live | faithfulness | llm_judge | n/a | 0/0 | 0 | >= 0.90 | proposed | n/a | - |
| critic | live | answer_relevancy | llm_judge | n/a | 0/0 | 0 | >= 0.80 | proposed | n/a | - |
| critic | live | hallucination_free | llm_judge | n/a | 0/0 | 0 | >= 0.90 | proposed | n/a | - |
| narrative | live | number_grounding (primary) | deterministic | 1.000 | 1/1 | 1 | >= 1.00 | design_b | PASS | - |
| narrative | live | narrative_restricted_terms | deterministic | 1.000 | 1/1 | 1 | >= 1.00 | design_b | PASS | - |
| narrative | live | conditions_traceable | deterministic | n/a | 0/0 | 0 | >= 1.00 | design_b | n/a | - |
| narrative | live | citation_validity | deterministic | n/a | 0/0 | 0 | >= 0.95 | design_b | n/a | - |
| narrative | live | narrative_faithfulness | llm_judge | n/a | 0/0 | 0 | >= 0.90 | design_b | n/a | - |
| narrative | live | faithfulness | llm_judge | 1.000 | 1/1 | 1 | >= 0.90 | proposed | PASS | - |
| narrative | live | answer_relevancy | llm_judge | 1.000 | 1/1 | 1 | >= 0.80 | proposed | PASS | - |
| narrative | live | hallucination_free | llm_judge | 0.750 | 0.75/1 | 1 | >= 0.90 | proposed | FAIL | hallucination_free 0.75 < 0.90: The score is 0.75 because the actual output mostly agrees with the contexts. Its statement that no risk assessment or recommendation can be completed is consistent with the locked result showing a composite and band of None both before and after the override. It also fits the case metadata for FCRM-2026-0090 (process_change, recommendation mode) and contradicts none of the version information. However, it wrongly claims the case record is empty with 'no risk factors', even though the context identifies 'customer' as the largest driver. That contradiction lowers the score. |
| examiner | live | examiner_grounding (primary) | deterministic | 1.000 | 24/24 | 5 | >= 1.00 | design_b | PASS | - |
| examiner | live | must_cite_coverage | deterministic | 0.889 | 8/9 | 3 | >= 0.90 | proposed | FAIL | no vote.revealed |
| examiner | live | refusal_rate | deterministic | 0.000 | 0/2 | 2 | >= 0.95 | design_b | FAIL | answered the unanswerable; answered the unanswerable |
| examiner | live | faithfulness | llm_judge | 1.000 | 5/5 | 5 | >= 0.90 | proposed | PASS | - |
| examiner | live | answer_relevancy | llm_judge | 1.000 | 5/5 | 5 | >= 0.80 | proposed | PASS | - |
| examiner | live | hallucination_free | llm_judge | 1.000 | 5/5 | 5 | >= 0.90 | proposed | PASS | - |
| watchlist | live | reflag_recall (primary) | deterministic | 0.000 | 0/4 | 3 | >= 1.00 | design_b | FAIL | missed ('FCRM-2026-0144', 'Algeria'); missed ('FCRM-2026-0147', 'Namibia') |
| watchlist | live | reflag_precision | deterministic | n/a | 0/0 | 0 | >= 1.00 | proposed | n/a | - |
| watchlist | live | citation_validity | deterministic | n/a | 0/0 | 0 | >= 0.95 | design_b | n/a | - |
| policy_gap | live | gap_targets_recorded_gap (primary) | deterministic | n/a | 0/0 | 0 | >= 1.00 | proposed | n/a | - |
| policy_gap | live | suggestion_novelty | deterministic | n/a | 0/0 | 0 | >= 1.00 | proposed | n/a | - |
| policy_gap | live | citation_validity | deterministic | n/a | 0/0 | 0 | >= 0.95 | design_b | n/a | - |
