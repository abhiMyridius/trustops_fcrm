"""S5's seed rows: the decisions already on record for the 80 seed cases, loaded
into the in-memory store and read back through its methods and the real routes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_contracts.enums import Outcome, Role, VoteOption
from fcrm_contracts.voting import tally_votes
from fcrm_decision.packs import content_hash
from fcrm_decision.seedmodel import (
    DELEGATED_PACK_PREFIX,
    RATIONALES,
    SeedDecisionError,
    SeedRows,
    build_seed_rows,
    committee_ballot,
    pack_id_for,
)
from fcrm_decision.store import InMemoryDecisionStore
from fcrm_seed.loader import cases, closed_cases, demo_case, load_raw, platform_outcome

COMMITTEE = [u["id"] for u in load_raw()["users"]["committee"]]
COMMITTEE_STAGE_CASES = ["FCRM-2026-0138", "FCRM-2026-0145"]
MIN_RATIONALE_CHARS = 20


def _rows() -> SeedRows:
    return build_seed_rows(cases(), COMMITTEE)


def _closed_committee() -> list[dict[str, Any]]:
    return [c for c in closed_cases() if c["score"]["route"] == "committee"]


def _closed_delegated() -> list[dict[str, Any]]:
    return [c for c in closed_cases() if c["score"]["route"] != "committee"]


async def _seeded_store() -> InMemoryDecisionStore:
    store = InMemoryDecisionStore()
    rows = _rows()
    await store.seed_if_empty(rows.packs, rows.votes, rows.outcomes)
    return store


# ------------------------------------------------------------------- shape
def test_there_is_one_outcome_for_each_of_the_65_closed_cases() -> None:
    rows = _rows()
    assert len(rows.outcomes) == 65
    assert {r.outcome.case_id for r in rows.outcomes} == {
        c["id"] for c in closed_cases()
    }
    assert Counter(r.mode for r in rows.outcomes) == {"delegated": 54, "committee": 11}


def test_the_split_follows_the_seed_route_which_follows_the_band() -> None:
    assert len(_closed_delegated()) == 54
    assert len(_closed_committee()) == 11
    assert all(c["score"]["band"] in (1, 2) for c in _closed_delegated())
    assert all(c["score"]["band"] in (3, 4) for c in _closed_committee())


def test_each_outcome_is_the_cases_recorded_outcome_and_never_a_no_majority() -> None:
    by_case = {r.outcome.case_id: r.outcome for r in _rows().outcomes}
    for case in closed_cases():
        outcome = by_case[case["id"]]
        assert outcome.outcome == Outcome(platform_outcome(case["outcome"]))
        assert outcome.no_majority is False
        assert outcome.round == 1


def test_delegated_outcomes_have_no_pack_only_a_sentinel() -> None:
    rows = _rows()
    delegated = [r for r in rows.outcomes if r.mode == "delegated"]
    packs = {p.pack_id for p in rows.packs}
    for row in delegated:
        assert row.outcome.pack_id == f"{DELEGATED_PACK_PREFIX}{row.outcome.case_id}"
        assert row.outcome.pack_id not in packs


def test_packs_are_the_eleven_decided_ones_and_the_two_committee_stage_ones() -> None:
    packs = _rows().packs
    assert len(packs) == 13
    decided = [p for p in packs if p.decided]
    open_ = [p for p in packs if not p.decided]
    assert {p.case_id for p in decided} == {c["id"] for c in _closed_committee()}
    assert sorted(p.case_id for p in open_) == COMMITTEE_STAGE_CASES
    assert all(p.pack_id == pack_id_for(p.case_id) and p.version == 1 for p in packs)


def test_the_demo_case_and_every_other_open_case_get_nothing() -> None:
    rows = _rows()
    touched = {p.case_id for p in rows.packs} | {
        r.outcome.case_id for r in rows.outcomes
    }
    assert demo_case()["id"] not in touched
    other_open = {c["id"] for c in cases() if c["stage"] not in ("closed", "committee")}
    assert not touched & other_open


# -------------------------------------------------------------- the ballots
def test_every_closed_committee_case_has_three_distinct_members_voting() -> None:
    rows = _rows()
    for case in _closed_committee():
        pack_id = pack_id_for(case["id"])
        votes = [v for v in rows.votes if v.pack_id == pack_id]
        assert [v.member_id for v in votes] == COMMITTEE
        assert {v.round for v in votes} == {1}
        assert all(v.voided is False for v in votes)
    assert len(rows.votes) == 33


def test_each_ballot_tallies_to_the_cases_outcome() -> None:
    rows = _rows()
    for case in _closed_committee():
        pack_id = pack_id_for(case["id"])
        ballot = [v.option.value for v in rows.votes if v.pack_id == pack_id]
        tallied, no_majority = tally_votes(ballot)
        assert no_majority is False
        assert tallied == platform_outcome(case["outcome"])


def test_ballots_are_a_mix_of_unanimous_and_two_to_one() -> None:
    rows = _rows()
    shapes = Counter()
    for case in _closed_committee():
        pack_id = pack_id_for(case["id"])
        options = Counter(v.option for v in rows.votes if v.pack_id == pack_id)
        shapes[tuple(sorted(options.values()))] += 1
    assert set(shapes) == {(3,), (1, 2)}


def test_every_rationale_is_from_the_fixed_list_for_its_option() -> None:
    for vote in _rows().votes:
        assert vote.rationale in RATIONALES[vote.option]
        assert len(vote.rationale) >= MIN_RATIONALE_CHARS
    assert all(
        len(text) >= MIN_RATIONALE_CHARS
        for texts in RATIONALES.values()
        for text in texts
    )


def test_votes_are_cast_after_the_pack_was_released() -> None:
    rows = _rows()
    released = {p.pack_id: p.released_at for p in rows.packs}
    for vote in rows.votes:
        assert released[vote.pack_id] is not None
        assert vote.cast_at > released[vote.pack_id]


def test_the_ballot_for_a_case_is_stable() -> None:
    first = committee_ballot("FCRM-2026-0150", VoteOption.APPROVE)
    assert first == committee_ballot("FCRM-2026-0150", VoteOption.APPROVE)
    assert first.count(VoteOption.APPROVE) >= 2


def test_rows_are_deterministic() -> None:
    assert _rows() == _rows()


# --------------------------------------------------------------------- packs
def test_the_snapshot_is_the_seed_score_with_four_passing_checks() -> None:
    by_case = {c["id"]: c for c in cases()}
    for pack in _rows().packs:
        score = by_case[pack.case_id]["score"]
        snapshot = pack.content
        assert snapshot["score"]["composite"] == score["composite"]
        assert snapshot["score"]["band"] == score["band"]
        assert snapshot["score"]["areas"].keys() == score["areas"].keys()
        assert all(snapshot["checks"].values())
        assert pack.checks.all_passed
        assert pack.released_at is not None


def test_committee_stage_packs_are_released_and_open_for_voting() -> None:
    for pack in (p for p in _rows().packs if not p.decided):
        assert pack.open_for_voting is True
        assert pack.current_round == 1
        assert pack.ballot_version == 0
        assert pack.superseded_by is None


def test_decided_packs_are_closed() -> None:
    for pack in (p for p in _rows().packs if p.decided):
        assert pack.open_for_voting is False
        assert pack.ballot_version == 3


# ----------------------------------------------------------------- the store
async def test_loading_fills_the_in_memory_store() -> None:
    store = InMemoryDecisionStore()
    rows = _rows()
    loaded = await store.seed_if_empty(rows.packs, rows.votes, rows.outcomes)
    assert loaded == {"packs": 13, "votes": 33, "outcomes": 65}
    for case in _closed_committee():
        pack = await store.latest_pack(case["id"])
        assert pack is not None
        assert pack.decided is True
        outcome = await store.get_outcome(pack.pack_id)
        assert outcome is not None
        assert outcome.outcome == Outcome(platform_outcome(case["outcome"]))
        assert await store.get_outcome_mode(pack.pack_id) == "committee"
        assert len(await store.votes_for_round(pack.pack_id, 1)) == 3


async def test_a_second_load_inserts_nothing() -> None:
    store = await _seeded_store()
    rows = _rows()
    again = await store.seed_if_empty(rows.packs, rows.votes, rows.outcomes)
    assert again == {"packs": 0, "votes": 0, "outcomes": 0}


async def test_delegated_outcomes_read_back_with_their_mode() -> None:
    store = await _seeded_store()
    case = _closed_delegated()[0]
    pack_id = f"{DELEGATED_PACK_PREFIX}{case['id']}"
    outcome = await store.get_outcome(pack_id)
    assert outcome is not None
    assert outcome.case_id == case["id"]
    assert await store.get_outcome_mode(pack_id) == "delegated"
    assert await store.latest_pack(case["id"]) is None


async def test_a_live_outcome_defaults_to_committee_mode() -> None:
    store = InMemoryDecisionStore()
    assert await store.get_outcome_mode("pack_unknown") is None


# ---------------------------------------------------------------- the routes
@pytest.fixture
async def seeded_client(decision_client: TestClient) -> TestClient:
    app: FastAPI = decision_client.app  # type: ignore[assignment]
    store = InMemoryDecisionStore()
    rows = _rows()
    await store.seed_if_empty(rows.packs, rows.votes, rows.outcomes)
    app.state.store = store
    return decision_client


async def test_a_decided_seeded_pack_reveals_its_votes_and_tally(
    seeded_client: TestClient, act_as: Callable[[Role, str], None]
) -> None:
    case = _closed_committee()[0]
    act_as(Role.ANALYST, case["analyst"])
    response = seeded_client.get(f"/packs/{pack_id_for(case['id'])}/votes")
    assert response.status_code == 200
    body = response.json()
    assert {v["member_id"] for v in body["votes"]} == set(COMMITTEE)
    assert sum(body["tally"].values()) == 3
    assert max(body["tally"], key=body["tally"].get) == platform_outcome(
        case["outcome"]
    )


async def test_a_decided_seeded_pack_refuses_further_votes(
    seeded_client: TestClient, cast_vote: Callable[..., Any]
) -> None:
    case = _closed_committee()[0]
    response = cast_vote(
        pack_id_for(case["id"]), COMMITTEE[0], "approve", rationale="x" * 30
    )
    assert response.status_code in (409, 422)


async def test_a_committee_stage_pack_is_live_and_collects_hidden_votes(
    seeded_client: TestClient, cast_vote: Callable[..., Any]
) -> None:
    pack_id = pack_id_for(COMMITTEE_STAGE_CASES[0])
    first = cast_vote(pack_id, COMMITTEE[0], "approve_with_conditions")
    assert first.status_code == 200, first.text
    assert first.json() == {
        "pack_id": pack_id,
        "round": 1,
        "votes_cast": 1,
        "quorum": 3,
    }


async def test_the_seeded_pack_serves_its_frozen_hash(
    seeded_client: TestClient, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-riyer")
    pack_id = pack_id_for(COMMITTEE_STAGE_CASES[0])
    body = seeded_client.get(f"/packs/{pack_id}").json()
    seeded = next(p for p in _rows().packs if p.pack_id == pack_id)
    assert body["content_hash"] == content_hash(seeded.content)
    assert body["released_at"] is not None
    assert body["checks"] == {
        "numbers_locked": True,
        "conditions_drafted": True,
        "all_review_items_decided": True,
        "boundary_and_band_acknowledged": True,
    }


# ------------------------------------------------------------------- guards
@pytest.mark.parametrize("members", [[], COMMITTEE[:2], [*COMMITTEE, "u-extra"]])
def test_quorum_is_exactly_three_members(members: list[str]) -> None:
    with pytest.raises(SeedDecisionError, match="quorum is 3"):
        build_seed_rows(cases(), members)


def test_committee_membership_comes_from_the_seed_users() -> None:
    assert COMMITTEE == ["u-amendes", "u-jokafor", "u-lchen"]
