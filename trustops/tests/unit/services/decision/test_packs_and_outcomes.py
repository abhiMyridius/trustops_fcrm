"""S5's pure logic: pack content hashing and the vote tally, including a
replay of the demo case's real 2-1 committee vote.
"""

from __future__ import annotations

from fcrm_contracts.enums import Outcome
from fcrm_decision.outcomes import tally_round
from fcrm_decision.packs import build_pack, content_hash, pack_unchanged
from fcrm_decision.release_checks import evaluate_release_checks
from fcrm_seed.loader import demo_case


def test_identical_content_hashes_identically() -> None:
    content = {"case_id": "C1", "locked_score": 10.4, "conditions": ["a", "b"]}
    assert content_hash(content) == content_hash(dict(reversed(content.items())))


def test_a_single_changed_value_changes_the_hash() -> None:
    a = {"case_id": "C1", "locked_score": 10.4}
    b = {"case_id": "C1", "locked_score": 10.5}
    assert content_hash(a) != content_hash(b)


def test_build_pack_round_trips_through_pack_unchanged() -> None:
    content = {"case_id": "FCRM-2026-0151", "composite": 10.4}
    pack = build_pack("FCRM-2026-0151", version=1, content=content)
    assert pack_unchanged(pack, content) is True

    tampered = {"case_id": "FCRM-2026-0151", "composite": 11.0}
    assert pack_unchanged(pack, tampered) is False


def test_release_checks_all_pass_only_when_every_one_does() -> None:
    passing = evaluate_release_checks(
        locked=True,
        all_items_reviewed=True,
        boundary_and_band_acknowledged=True,
        business_summary_present=True,
    )
    assert passing.all_passed is True
    assert passing.failing() == []

    failing = evaluate_release_checks(
        locked=True,
        all_items_reviewed=False,
        boundary_and_band_acknowledged=True,
        business_summary_present=False,
    )
    assert failing.all_passed is False
    assert failing.failing() == ["all_items_reviewed", "business_summary_present"]


def test_the_demo_case_s_vote_tallies_to_approve_with_conditions() -> None:
    """Joseph Okafor and Ana Mendes vote approve_with_conditions; Lin Chen
    defers. Two of three agree, so that is the outcome — never a
    'majority wins by any rule', specifically the two matching votes.
    (docs/requirements/research/06-walkthrough-seed-extraction.md §3.8)
    """
    demo = demo_case()
    votes = [v["vote"] for v in demo["votes"]]
    assert votes == ["approve_with_conditions", "approve_with_conditions", "defer"]

    result = tally_round(votes, round_number=1)
    assert result.outcome == Outcome.APPROVE_WITH_CONDITIONS
    assert result.no_majority is False
    assert result.next_round is None
    assert result.outcome.value == demo["outcome"]


def test_a_hypothetical_three_way_split_reopens_a_round() -> None:
    result = tally_round(["approve", "defer", "reject"], round_number=1)
    assert result.outcome is None
    assert result.no_majority is True
    assert result.next_round == 2


def test_two_approves_and_a_conditions_vote_resolve_to_conditions_proposed() -> None:
    """The proposed approve-family refinement (``fcrm_contracts.voting``): a
    member who asked for conditions is never outvoted into a plain approval.
    """
    result = tally_round(["approve", "approve", "approve_with_conditions"], 1)
    assert result.outcome == Outcome.APPROVE_WITH_CONDITIONS
    assert result.next_round is None
