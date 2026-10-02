# Eval index: which datasets guard which prompt

A change to any prompt id below must pass its agent's gated metrics before `prompts/MANIFEST.yaml` moves (design B §6.4, gate G-T1). Datasets live under `evals/datasets/<agent>/`. Metric definitions and thresholds are in design B §7.3. Nothing here is measured yet: the first baselines are planned for 5–6 Oct (design B §7.6).

| Agent | Prompt ids guarded | Datasets | Primary metric (promotion) | Other gated metrics |
|---|---|---|---|---|
| screening | screening.system, screening.user | screening/fast-lane-100 | fast_lane_precision ≥ 0.90 | fast_lane_recall ≥ 0.90, citation_validity ≥ 0.95 |
| intake | intake.system, intake.user | intake/extraction-20 | field_extraction_accuracy ≥ 0.90 | source_span_accuracy ≥ 0.90, category_f1 ≥ 0.90, gap_question_precision ≥ 0.90, citation_validity ≥ 0.95 |
| research | research.system, research.user | research/clauses-40, research/gaps-12 | clause_recall_at_k ≥ 0.85 | citation_validity ≥ 0.95, clause_precision ≥ 0.70, gap_honesty ≥ 0.90 with 0 forbidden clauses |
| assessment | assessment.system, assessment.user | assessment/ratings-80 | li_within_one ≥ 0.85 | control_match_recall ≥ 0.90, stale_evidence_detection ≥ 0.95, planned_never_rated = 1.00, citation_validity ≥ 0.95 |
| critic | critic.system, critic.user | critic/planted-60 | planted_error_detection ≥ 0.85 | finding_precision (judge) ≥ 0.80, citation_validity ≥ 0.95 |
| narrative (completeness) | narrative.system, narrative.completeness.user | narrative/completeness-30 | question_grounding ≥ 0.95 | question_dedupe (judge) ≥ 0.90 |
| narrative (recommendation) | narrative.system, narrative.recommendation.user | narrative/recommendation-40 | narrative_faithfulness (judge) ≥ 0.90 | number_grounding = 1.00, restricted_terms_zero = 0 hits, conditions_map_to_weak_controls = 1.00 |
| examiner | examiner.system, examiner.user | examiner/questions-120 | sentence_record_grounding = 1.00 | refusal_rate ≥ 0.95, citation_validity ≥ 0.95 |
| watchlist | watchlist.system, watchlist.user | watchlist/reflag-24 | reflag_recall = 1.00 | citation_validity ≥ 0.95 |
| policy_gap | policy_gap.system, policy_gap.user | policy_gap/suggestions-14 | coverage_check_correctness | suggestion_acceptance (live, tracked, no gate until 10 reviewed) |

`narrative.system` is shared by both narrative modes, so a change to it must pass both rows.

Guardrail sets (deterministic, run in every push gate): `redteam/injection-50` (injection_block_rate = 1.00) and `redteam/pii-20` (pii_mask_recall ≥ 0.95), against `guards/injection_patterns.txt` and `guards/pii_entities.yaml`.
