# TrustOps eval report: offline self-check

Generated 2026-10-03T21:39:36.912745+00:00 from 18 result(s).

Self-check run: `echo` scores each dataset's own reference outputs (must be 1.000 everywhere), `degraded` scores the same outputs with deliberate mistakes. Neither is a measurement of a real agent; the seed's weekly per-agent figures (`seed.json["evals"]`) remain seeded targets until a live or recorded run replaces them.

## Agents

| Agent | Dataset | Rows | Producer | Primary metric | Score | Threshold | Result | Other metrics |
|---|---|---|---|---|---|---|---|---|
| screening | screening/fast-lane-100 | 100 | echo | fast_lane_precision | 1.000 | >= 0.90 | PASS | fast_lane_recall 1.000 (>= 0.90) PASS; citation_validity 1.000 (>= 0.95) PASS |
| screening | screening/fast-lane-100 | 100 | degraded | fast_lane_precision | 0.471 | >= 0.90 | FAIL | fast_lane_recall 0.889 (>= 0.90) FAIL; citation_validity 1.000 (>= 0.95) PASS |
| intake | intake/extraction-80 | 80 | echo | field_extraction_accuracy | 1.000 | >= 0.90 | PASS | source_span_accuracy 1.000 (>= 0.90) PASS; category_f1 1.000 (>= 0.90) PASS; gap_question_precision 1.000 (>= 0.90) PASS; citation_validity 1.000 (>= 0.95) PASS |
| intake | intake/extraction-80 | 80 | degraded | field_extraction_accuracy | 0.778 | >= 0.90 | FAIL | source_span_accuracy 1.000 (>= 0.90) PASS; category_f1 0.835 (>= 0.90) FAIL; gap_question_precision 1.000 (>= 0.90) PASS; citation_validity 1.000 (>= 0.95) PASS |
| research | research/clauses-42 | 42 | echo | clause_recall | 1.000 | >= 0.85 | PASS | clause_precision 1.000 (>= 0.70) PASS; precedent_top1 1.000 (>= 0.90) PASS; citation_validity 1.000 (>= 0.95) PASS |
| research | research/clauses-42 | 42 | degraded | clause_recall | 0.656 | >= 0.85 | FAIL | clause_precision 1.000 (>= 0.70) PASS; precedent_top1 1.000 (>= 0.90) PASS; citation_validity 0.475 (>= 0.95) FAIL |
| assessment | assessment/ratings-80 | 80 | echo | li_within_one | 1.000 | >= 0.85 | PASS | control_match_precision 1.000 (>= 0.90) PASS; control_match_recall 1.000 (>= 0.90) PASS; evidence_date_exact 1.000 (>= 0.95) PASS; citation_validity 1.000 (>= 0.95) PASS |
| assessment | assessment/ratings-80 | 80 | degraded | li_within_one | 0.412 | >= 0.85 | FAIL | control_match_precision 0.908 (>= 0.90) PASS; control_match_recall 1.000 (>= 0.90) PASS; evidence_date_exact 0.882 (>= 0.95) FAIL; citation_validity 1.000 (>= 0.95) PASS |
| critic | critic/planted-61 | 61 | echo | planted_error_detection | 1.000 | >= 0.85 | PASS | critic_finding_groundedness 1.000 (>= 1.00) PASS; citation_validity 1.000 (>= 0.95) PASS |
| critic | critic/planted-61 | 61 | degraded | planted_error_detection | 0.629 | >= 0.85 | FAIL | critic_finding_groundedness 0.016 (>= 1.00) FAIL; citation_validity 1.000 (>= 0.95) PASS |
| narrative | narrative/recommendation-66 | 66 | echo | number_grounding | 1.000 | >= 1.00 | PASS | narrative_restricted_terms 1.000 (>= 1.00) PASS; conditions_traceable 1.000 (>= 1.00) PASS; citation_validity 1.000 (>= 0.95) PASS; narrative_faithfulness 1.000 (>= 0.90) PASS |
| narrative | narrative/recommendation-66 | 66 | degraded | number_grounding | 0.091 | >= 1.00 | FAIL | narrative_restricted_terms 0.000 (>= 1.00) FAIL; conditions_traceable 0.667 (>= 1.00) FAIL; citation_validity 1.000 (>= 0.95) PASS; narrative_faithfulness 0.000 (>= 0.90) FAIL |
| examiner | examiner/questions-5 | 5 | echo | examiner_grounding | 1.000 | >= 1.00 | PASS | must_cite_coverage 1.000 (>= 0.90) PASS; refusal_rate 1.000 (>= 0.95) PASS |
| examiner | examiner/questions-5 | 5 | degraded | examiner_grounding | 0.786 | >= 1.00 | FAIL | must_cite_coverage 0.667 (>= 0.90) FAIL; refusal_rate 0.000 (>= 0.95) FAIL |
| watchlist | watchlist/reflag-29 | 29 | echo | reflag_recall | 1.000 | >= 1.00 | PASS | reflag_precision 1.000 (>= 1.00) PASS; citation_validity 1.000 (>= 0.95) PASS |
| watchlist | watchlist/reflag-29 | 29 | degraded | reflag_recall | 0.250 | >= 1.00 | FAIL | reflag_precision 0.037 (>= 1.00) FAIL; citation_validity 1.000 (>= 0.95) PASS |
| policy_gap | policy_gap/suggestions-1 | 1 | echo | gap_targets_recorded_gap | 1.000 | >= 1.00 | PASS | suggestion_novelty 1.000 (>= 1.00) PASS; citation_validity 1.000 (>= 0.95) PASS |
| policy_gap | policy_gap/suggestions-1 | 1 | degraded | gap_targets_recorded_gap | 0.000 | >= 1.00 | FAIL | suggestion_novelty 0.000 (>= 1.00) FAIL; citation_validity 1.000 (>= 0.95) PASS |

## Promotion gate (design B §6.4)

| Run | Agent | Primary metric | Baseline | Candidate | Tolerance | Verdict | Reasons |
|---|---|---|---|---|---|---|---|
| screening: echo vs degraded baseline | screening | fast_lane_precision | 0.471 | 1.000 | 0.0 | PASS | - |
| screening: degraded vs echo baseline | screening | fast_lane_precision | 1.000 | 0.471 | 0.0 | FAIL | fast_lane_precision 0.471 is below its threshold 0.90; fast_lane_precision 0.471 is below the baseline 1.000 (tolerance 0.0) |
| intake: echo vs degraded baseline | intake | field_extraction_accuracy | 0.778 | 1.000 | 0.0 | PASS | - |
| intake: degraded vs echo baseline | intake | field_extraction_accuracy | 1.000 | 0.778 | 0.0 | FAIL | field_extraction_accuracy 0.778 is below its threshold 0.90; field_extraction_accuracy 0.778 is below the baseline 1.000 (tolerance 0.0) |
| research: echo vs degraded baseline | research | clause_recall | 0.656 | 1.000 | 0.0 | PASS | - |
| research: degraded vs echo baseline | research | clause_recall | 1.000 | 0.656 | 0.0 | FAIL | clause_recall 0.656 is below its threshold 0.85; clause_recall 0.656 is below the baseline 1.000 (tolerance 0.0) |
| assessment: echo vs degraded baseline | assessment | li_within_one | 0.412 | 1.000 | 0.0 | PASS | - |
| assessment: degraded vs echo baseline | assessment | li_within_one | 1.000 | 0.412 | 0.0 | FAIL | li_within_one 0.412 is below its threshold 0.85; li_within_one 0.412 is below the baseline 1.000 (tolerance 0.0) |
| critic: echo vs degraded baseline | critic | planted_error_detection | 0.629 | 1.000 | 0.0 | PASS | - |
| critic: degraded vs echo baseline | critic | planted_error_detection | 1.000 | 0.629 | 0.0 | FAIL | planted_error_detection 0.629 is below its threshold 0.85; planted_error_detection 0.629 is below the baseline 1.000 (tolerance 0.0) |
| narrative: echo vs degraded baseline | narrative | number_grounding | 0.091 | 1.000 | 0.0 | PASS | - |
| narrative: degraded vs echo baseline | narrative | number_grounding | 1.000 | 0.091 | 0.0 | FAIL | number_grounding 0.091 is below its threshold 1.00; number_grounding 0.091 is below the baseline 1.000 (tolerance 0.0) |
| examiner: echo vs degraded baseline | examiner | examiner_grounding | 0.786 | 1.000 | 0.0 | PASS | - |
| examiner: degraded vs echo baseline | examiner | examiner_grounding | 1.000 | 0.786 | 0.0 | FAIL | examiner_grounding 0.786 is below its threshold 1.00; examiner_grounding 0.786 is below the baseline 1.000 (tolerance 0.0) |
| watchlist: echo vs degraded baseline | watchlist | reflag_recall | 0.250 | 1.000 | 0.0 | PASS | - |
| watchlist: degraded vs echo baseline | watchlist | reflag_recall | 1.000 | 0.250 | 0.0 | FAIL | reflag_recall 0.250 is below its threshold 1.00; reflag_recall 0.250 is below the baseline 1.000 (tolerance 0.0) |
| policy_gap: echo vs degraded baseline | policy_gap | gap_targets_recorded_gap | 0.000 | 1.000 | 0.0 | PASS | - |
| policy_gap: degraded vs echo baseline | policy_gap | gap_targets_recorded_gap | 1.000 | 0.000 | 0.0 | FAIL | gap_targets_recorded_gap 0.000 is below its threshold 1.00; gap_targets_recorded_gap 0.000 is below the baseline 1.000 (tolerance 0.0) |

## Metric detail

| Agent | Producer | Metric | Kind | Score | Num/Den | Rows | Threshold | Source | Result | First flags |
|---|---|---|---|---|---|---|---|---|---|---|
| screening | echo | fast_lane_precision (primary) | deterministic | 1.000 | 18/18 | 18 | >= 0.90 | design_b | PASS | - |
| screening | echo | fast_lane_recall | deterministic | 1.000 | 18/18 | 18 | >= 0.90 | design_b | PASS | - |
| screening | echo | citation_validity | deterministic | 1.000 | 100/100 | 100 | >= 0.95 | design_b | PASS | - |
| screening | degraded | fast_lane_precision (primary) | deterministic | 0.471 | 16/34 | 34 | >= 0.90 | design_b | FAIL | FCRM-2026-0074: false positive; FCRM-2026-0081: false positive |
| screening | degraded | fast_lane_recall | deterministic | 0.889 | 16/18 | 18 | >= 0.90 | design_b | FAIL | FCRM-2026-0140: missed; FCRM-2026-0089~vol-4.2M: missed |
| screening | degraded | citation_validity | deterministic | 1.000 | 100/100 | 100 | >= 0.95 | design_b | PASS | - |
| intake | echo | field_extraction_accuracy (primary) | deterministic | 1.000 | 9/9 | 1 | >= 0.90 | design_b | PASS | - |
| intake | echo | source_span_accuracy | deterministic | 1.000 | 9/9 | 1 | >= 0.90 | design_b | PASS | - |
| intake | echo | category_f1 | deterministic | 1.000 | 240/240 | 80 | >= 0.90 | design_b | PASS | - |
| intake | echo | gap_question_precision | deterministic | 1.000 | 3/3 | 1 | >= 0.90 | design_b | PASS | - |
| intake | echo | citation_validity | deterministic | 1.000 | 129/129 | 80 | >= 0.95 | design_b | PASS | - |
| intake | degraded | field_extraction_accuracy (primary) | deterministic | 0.778 | 7/9 | 1 | >= 0.90 | design_b | FAIL | F01: 'Priya Nair, Payments (approx.)'; F06: 'Remesas del Norte S.A. (approx.)' |
| intake | degraded | source_span_accuracy | deterministic | 1.000 | 9/9 | 1 | >= 0.90 | design_b | PASS | - |
| intake | degraded | category_f1 | deterministic | 0.835 | 172/206 | 80 | >= 0.90 | design_b | FAIL | fn ['process_change']; fn ['process_change'] |
| intake | degraded | gap_question_precision | deterministic | 1.000 | 3/3 | 1 | >= 0.90 | design_b | PASS | - |
| intake | degraded | citation_validity | deterministic | 1.000 | 95/95 | 58 | >= 0.95 | design_b | PASS | - |
| research | echo | clause_recall (primary) | deterministic | 1.000 | 122/122 | 42 | >= 0.85 | design_b | PASS | - |
| research | echo | clause_precision | deterministic | 1.000 | 122/122 | 42 | >= 0.70 | design_b | PASS | - |
| research | echo | precedent_top1 | deterministic | 1.000 | 1/1 | 1 | >= 0.90 | proposed | PASS | - |
| research | echo | citation_validity | deterministic | 1.000 | 122/122 | 42 | >= 0.95 | design_b | PASS | - |
| research | degraded | clause_recall (primary) | deterministic | 0.656 | 80/122 | 42 | >= 0.85 | design_b | FAIL | missed OFAC-1.2; missed OFAC-1.2 |
| research | degraded | clause_precision | deterministic | 1.000 | 80/80 | 42 | >= 0.70 | design_b | PASS | - |
| research | degraded | precedent_top1 | deterministic | 1.000 | 1/1 | 1 | >= 0.90 | proposed | PASS | - |
| research | degraded | citation_validity | deterministic | 0.475 | 38/80 | 42 | >= 0.95 | design_b | FAIL | outputs[0]: quote_verbatim (none: quote not found verbatim in the cited source); outputs[0]: quote_verbatim (none: quote not found verbatim in the cited source) |
| assessment | echo | li_within_one (primary) | deterministic | 1.000 | 136/136 | 80 | >= 0.85 | design_b | PASS | - |
| assessment | echo | control_match_precision | deterministic | 1.000 | 679/679 | 80 | >= 0.90 | proposed | PASS | - |
| assessment | echo | control_match_recall | deterministic | 1.000 | 679/679 | 80 | >= 0.90 | design_b | PASS | - |
| assessment | echo | evidence_date_exact | deterministic | 1.000 | 679/679 | 80 | >= 0.95 | proposed | PASS | - |
| assessment | echo | citation_validity | deterministic | 1.000 | 815/815 | 80 | >= 0.95 | design_b | PASS | - |
| assessment | degraded | li_within_one (primary) | deterministic | 0.412 | 56/136 | 80 | >= 0.85 | design_b | FAIL | product: 5x3 vs 2x3; customer: 1x4 vs 5x4 |
| assessment | degraded | control_match_precision | deterministic | 0.908 | 679/748 | 80 | >= 0.90 | proposed | PASS | extra ('customer', 'CTL-C06'); extra ('geography', 'CTL-G04') |
| assessment | degraded | control_match_recall | deterministic | 1.000 | 679/679 | 80 | >= 0.90 | design_b | PASS | - |
| assessment | degraded | evidence_date_exact | deterministic | 0.882 | 599/679 | 80 | >= 0.95 | proposed | FAIL | CTL-P05: 2019-01-01 vs 2025-11-20; CTL-C04: 2019-01-01 vs 2026-03-31 |
| assessment | degraded | citation_validity | deterministic | 1.000 | 884/884 | 80 | >= 0.95 | design_b | PASS | - |
| critic | echo | planted_error_detection (primary) | deterministic | 1.000 | 62/62 | 61 | >= 0.85 | design_b | PASS | - |
| critic | echo | critic_finding_groundedness | deterministic | 1.000 | 62/62 | 61 | >= 1.00 | proposed | PASS | - |
| critic | echo | citation_validity | deterministic | 1.000 | 62/62 | 61 | >= 0.95 | design_b | PASS | - |
| critic | degraded | planted_error_detection (primary) | deterministic | 0.629 | 39/62 | 61 | >= 0.85 | design_b | FAIL | missed precedent_deviation on CTL-P05; missed evidence_recency on CTL-P05 |
| critic | degraded | critic_finding_groundedness | deterministic | 0.016 | 1/62 | 61 | >= 1.00 | proposed | FAIL | unresolved refs ['#999']; unresolved refs ['#999'] |
| critic | degraded | citation_validity | deterministic | 1.000 | 62/62 | 61 | >= 0.95 | design_b | PASS | - |
| narrative | echo | number_grounding (primary) | deterministic | 1.000 | 132/132 | 66 | >= 1.00 | design_b | PASS | - |
| narrative | echo | narrative_restricted_terms | deterministic | 1.000 | 66/66 | 66 | >= 1.00 | design_b | PASS | - |
| narrative | echo | conditions_traceable | deterministic | 1.000 | 3/3 | 1 | >= 1.00 | design_b | PASS | - |
| narrative | echo | citation_validity | deterministic | 1.000 | 66/66 | 66 | >= 0.95 | design_b | PASS | - |
| narrative | echo | narrative_faithfulness | llm_judge | 1.000 | 66/66 | 66 | >= 0.90 | design_b | PASS | - |
| narrative | degraded | number_grounding (primary) | deterministic | 0.091 | 12/132 | 66 | >= 1.00 | design_b | FAIL | outputs[0]: ungrounded: 12.5; outputs[0]: ungrounded: 12.5 |
| narrative | degraded | narrative_restricted_terms | deterministic | 0.000 | 0/66 | 66 | >= 1.00 | design_b | FAIL | restricted: band; restricted: band |
| narrative | degraded | conditions_traceable | deterministic | 0.667 | 2/3 | 1 | >= 1.00 | design_b | FAIL | CND-1: untraceable source 'analyst preference' |
| narrative | degraded | citation_validity | deterministic | 1.000 | 66/66 | 66 | >= 0.95 | design_b | PASS | - |
| narrative | degraded | narrative_faithfulness | llm_judge | 0.000 | 0/66 | 66 | >= 0.90 | design_b | FAIL | judge 0.60 < 0.8; judge 0.53 < 0.8 |
| examiner | echo | examiner_grounding (primary) | deterministic | 1.000 | 12/12 | 3 | >= 1.00 | design_b | PASS | - |
| examiner | echo | must_cite_coverage | deterministic | 1.000 | 9/9 | 3 | >= 0.90 | proposed | PASS | - |
| examiner | echo | refusal_rate | deterministic | 1.000 | 2/2 | 2 | >= 0.95 | design_b | PASS | - |
| examiner | degraded | examiner_grounding (primary) | deterministic | 0.786 | 11/14 | 5 | >= 1.00 | design_b | FAIL | ungrounded [999]: The composite residual was first computed at 9.8, Band 2, wh; ungrounded [999]: Analyst Rahul Iyer overrode the vendor due-diligence rating  |
| examiner | degraded | must_cite_coverage | deterministic | 0.667 | 6/9 | 3 | >= 0.90 | proposed | FAIL | no score.computed; no human.override |
| examiner | degraded | refusal_rate | deterministic | 0.000 | 0/2 | 2 | >= 0.95 | design_b | FAIL | answered the unanswerable; answered the unanswerable |
| watchlist | echo | reflag_recall (primary) | deterministic | 1.000 | 4/4 | 3 | >= 1.00 | design_b | PASS | - |
| watchlist | echo | reflag_precision | deterministic | 1.000 | 4/4 | 3 | >= 1.00 | proposed | PASS | - |
| watchlist | echo | citation_validity | deterministic | 1.000 | 4/4 | 3 | >= 0.95 | design_b | PASS | - |
| watchlist | degraded | reflag_recall (primary) | deterministic | 0.250 | 1/4 | 3 | >= 1.00 | design_b | FAIL | missed ('FCRM-2026-0147', 'Namibia'); missed ('FCRM-2026-0144', 'Algeria') |
| watchlist | degraded | reflag_precision | deterministic | 0.037 | 1/27 | 27 | >= 1.00 | proposed | FAIL | invented ('FCRM-2026-0136', 'Bosnia and Herzegovina'); invented ('FCRM-2026-0136', 'Angola') |
| watchlist | degraded | citation_validity | deterministic | 1.000 | 27/27 | 27 | >= 0.95 | design_b | PASS | - |
| policy_gap | echo | gap_targets_recorded_gap (primary) | deterministic | 1.000 | 1/1 | 1 | >= 1.00 | proposed | PASS | - |
| policy_gap | echo | suggestion_novelty | deterministic | 1.000 | 1/1 | 1 | >= 1.00 | proposed | PASS | - |
| policy_gap | echo | citation_validity | deterministic | 1.000 | 1/1 | 1 | >= 0.95 | design_b | PASS | - |
| policy_gap | degraded | gap_targets_recorded_gap (primary) | deterministic | 0.000 | 0/1 | 1 | >= 1.00 | proposed | FAIL | GAP-UNKNOWN/channel: not a recorded gap |
| policy_gap | degraded | suggestion_novelty | deterministic | 0.000 | 0/1 | 1 | >= 1.00 | proposed | FAIL | GAP-UNKNOWN: reproduces an existing clause |
| policy_gap | degraded | citation_validity | deterministic | 1.000 | 1/1 | 1 | >= 0.95 | design_b | PASS | - |
