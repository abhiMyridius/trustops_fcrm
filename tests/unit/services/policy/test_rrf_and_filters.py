"""Reciprocal rank fusion and Index Contract metadata validation —
verified by hand-computed worked examples, not just round-trip checks.
"""

from __future__ import annotations

import pytest
from fcrm_policy.search.filters import matches_filters, validate_metadata
from fcrm_policy.search.rrf import reciprocal_rank_fusion, top_k


def test_a_document_at_rank_1_in_both_lists_scores_highest() -> None:
    semantic = ["A", "B", "C"]
    keyword = ["A", "C", "B"]
    fused = reciprocal_rank_fusion([semantic, keyword])
    assert fused[0][0] == "A"


def test_the_fused_score_matches_the_rrf_formula_by_hand() -> None:
    """k=60: A is rank 1 in both lists -> 1/61 + 1/61 = 2/61.
    B is rank 2 in the first list only -> 1/62.
    """
    fused = dict(reciprocal_rank_fusion([["A", "B"], ["A"]], k=60))
    assert fused["A"] == pytest.approx(2 / 61)
    assert fused["B"] == pytest.approx(1 / 62)


def test_a_document_in_only_one_list_still_gets_a_score() -> None:
    fused = dict(reciprocal_rank_fusion([["A"], ["B"]]))
    assert fused["A"] > 0
    assert fused["B"] > 0


def test_top_k_truncates_and_drops_scores() -> None:
    result = top_k([["A", "B", "C"], ["A", "B", "C"]], k=2)
    assert result == ["A", "B"]


def test_ties_break_deterministically_by_id() -> None:
    """Two documents that never co-occur each only appear in one list at
    the same rank, so their fused scores tie; the order must still be
    reproducible (alphabetical by id), not whatever dict iteration gives.
    """
    fused = reciprocal_rank_fusion([["Z"], ["A"]])
    assert [doc_id for doc_id, _ in fused] == ["A", "Z"]


def test_validate_metadata_accepts_the_five_index_contract_keys() -> None:
    assert (
        validate_metadata(
            {
                "corpus_version": 14,
                "trigger_type": "vendor_onboarding",
                "risk_area": "channel",
                "jurisdiction": "MX",
                "closed_date": "2026-06-15",
            }
        )
        == []
    )


def test_validate_metadata_rejects_an_unknown_key() -> None:
    problems = validate_metadata({"corpus_version": 14, "made_up_key": "oops"})
    assert problems == ["made_up_key"]


def test_matches_filters_ignores_none_valued_filters() -> None:
    metadata = {"jurisdiction": "MX", "risk_area": "channel"}
    assert matches_filters(metadata, {"jurisdiction": "MX", "risk_area": None}) is True
    assert matches_filters(metadata, {"jurisdiction": "US"}) is False
