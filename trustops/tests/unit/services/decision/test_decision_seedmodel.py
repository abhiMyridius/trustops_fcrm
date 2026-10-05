"""S5's seed rows: the decisions already on record for the 80 seed cases, loaded
into the in-memory store and read back through its methods and the real routes."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from fcrm_contracts.api.decision import PackContent
from fcrm_contracts.enums import Outcome, Role, VoteOption
from fcrm_contracts.voting import tally_votes
from fcrm_decision.packs import content_hash
from fcrm_decision.seedmodel import (
    DELEGATED_PACK_PREFIX,
    RATIONALES,
    SeedDecisionError,
    SeedReference,
    SeedRows,
    build_seed_rows,
    committee_ballot,
    pack_id_for,
)
from fcrm_decision.store_memory import InMemoryDecisionStore
from fcrm_seed.loader import cases, closed_cases, demo_case, load_raw, platform_outcome

COMMITTEE = [u["id"] for u in load_raw()["users"]["committee"]]
COMMITTEE_STAGE_CASES = ["FCRM-2026-0138", "FCRM-2026-0145"]
MIN_RATIONALE_CHARS = 20


def _rows() -> SeedRows:
    return build_seed_rows(cases(), COMMITTEE, SeedReference.from_seed(load_raw()))


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


def test_every_outcome_is_dated_the_day_the_case_was_decided() -> None:
    decided = {c["id"]: c["decided"] for c in closed_cases()}
    for row in _rows().outcomes:
        assert row.decided_at is not None
        assert row.decided_at.date().isoformat() == decided[row.outcome.case_id]


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
    shapes: Counter[tuple[int, ...]] = Counter()
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
        assert [a["domain"] for a in snapshot["score"]["areas"]] == list(score["areas"])
        assert all(snapshot["checks"].values())
        assert pack.checks.all_passed
        assert pack.released_at is not None


def test_every_seeded_snapshot_is_a_valid_pack_body_with_names_and_labels() -> None:
    by_case = {c["id"]: c for c in cases()}
    names = {p["id"]: p["name"] for g in load_raw()["users"].values() for p in g}
    for pack in _rows().packs:
        content = PackContent.model_validate(pack.content)
        case = by_case[pack.case_id]
        assert content.title == case["title"]
        assert content.analyst_name == names[case["analyst"]]
        assert content.owner_name == names[case["owner"]]
        assert content.score.band_label in {"Low", "Moderate", "Elevated", "High"}
        assert content.change_types == case["change_types"]
        assert content.pins.scoring == "v3"
        assert pack.content_hash == content_hash(pack.content)


def test_committee_stage_packs_are_released_and_open_for_voting() -> None:
    for pack in (p for p in _rows().packs if not p.decided):
        assert pack.open_for_voting is True
        assert pack.current_round == 1
        assert pack.ballot_version == 0
        assert pack.superseded is False
        assert pack.released_to == COMMITTEE


def test_decided_packs_are_closed() -> None:
    for pack in (p for p in _rows().packs if p.decided):
        assert pack.open_for_voting is False
        assert pack.ballot_version == 3
        assert pack.status == "decided"


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
        assert outcome.outcome.outcome == Outcome(platform_outcome(case["outcome"]))
        assert outcome.mode == "committee"
        assert len(await store.round_votes(pack.pack_id, 1)) == 3


async def test_a_second_load_inserts_nothing() -> None:
    store = await _seeded_store()
    rows = _rows()
    again = await store.seed_if_empty(rows.packs, rows.votes, rows.outcomes)
    assert again == {"packs": 0, "votes": 0, "outcomes": 0}


async def test_delegated_outcomes_read_back_with_their_mode() -> None:
    store = await _seeded_store()
    case = _closed_delegated()[0]
    outcome = await store.case_outcome(case["id"])
    assert outcome is not None
    assert outcome.mode == "delegated"
    assert outcome.outcome.pack_id == f"{DELEGATED_PACK_PREFIX}{case['id']}"
    assert await store.latest_pack(case["id"]) is None


async def test_an_unknown_pack_has_no_outcome() -> None:
    store = InMemoryDecisionStore()
    assert await store.get_outcome("pack_unknown") is None


# ---------------------------------------------------------------- the routes
@pytest.fixture
async def seeded_client(decision_client: TestClient) -> TestClient:
    app: FastAPI = decision_client.app  # type: ignore[assignment]
    app.state.store = await _seeded_store()
    return decision_client


async def test_a_decided_seeded_pack_reveals_its_votes_and_tally(
    seeded_client: TestClient, act_as: Callable[[Role, str], None]
) -> None:
    case = _closed_committee()[0]
    act_as(Role.ANALYST, case["analyst"])

    response = seeded_client.get(f"/packs/{pack_id_for(case['id'])}/votes")

    assert response.status_code == 200
    body = response.json()
    assert body["revealed"] is True
    assert body["cast_count"] == 3
    assert sum(body["tally"].values()) == 3
    assert body["outcome"] == platform_outcome(case["outcome"])
    assert [m["name"] for m in body["members"]] == [
        "Ana Mendes",
        "Joseph Okafor",
        "Lin Chen",
    ]
    assert all(m["choice"] is not None for m in body["members"])


async def test_a_decided_seeded_pack_refuses_further_votes(
    seeded_client: TestClient, cast_vote: Callable[..., Any]
) -> None:
    case = _closed_committee()[0]
    response = cast_vote(pack_id_for(case["id"]), COMMITTEE[0], "approve")
    assert response.status_code == 409
    assert response.json()["type"].endswith("/pack_decided")


async def test_a_committee_stage_pack_is_live_and_collects_hidden_votes(
    seeded_client: TestClient, cast_vote: Callable[..., Any]
) -> None:
    pack_id = pack_id_for(COMMITTEE_STAGE_CASES[0])
    first = cast_vote(pack_id, COMMITTEE[0], "approve_with_conditions")
    assert first.status_code == 201, first.text
    assert first.json()["round"] == 1
    assert first.json()["cast_n_of_3"] == 1


async def test_the_seeded_pack_serves_its_frozen_hash_and_names(
    seeded_client: TestClient, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-riyer")
    pack_id = pack_id_for(COMMITTEE_STAGE_CASES[0])

    body = seeded_client.get(f"/packs/{pack_id}").json()

    seeded = next(p for p in _rows().packs if p.pack_id == pack_id)
    assert body["content_hash"] == content_hash(seeded.content)
    assert body["released_at"] is not None
    assert body["status"] == "released"
    assert body["analyst_name"] == "Rahul Iyer"
    assert body["score"]["band_label"] in {"Elevated", "High"}


async def test_the_committee_queue_lists_the_two_open_and_the_eleven_decided(
    seeded_client: TestClient, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.COMMITTEE, COMMITTEE[0])

    items = seeded_client.get("/committee/queue").json()["items"]

    assert len(items) == 13
    open_ = [i for i in items if i["status"] == "open"]
    decided = [i for i in items if i["status"] == "decided"]
    assert sorted(i["case_id"] for i in open_) == COMMITTEE_STAGE_CASES
    assert len(decided) == 11
    assert all(i["your_vote"] == "not_voted" and i["cast_count"] == 0 for i in open_)
    assert all(i["your_vote"] == "voted" and i["cast_count"] == 3 for i in decided)
    assert all(i["outcome_label"] for i in decided)


# ------------------------------------------------------------------- guards
@pytest.mark.parametrize("members", [[], COMMITTEE[:2], [*COMMITTEE, "u-extra"]])
def test_quorum_is_exactly_three_members(members: list[str]) -> None:
    with pytest.raises(SeedDecisionError, match="quorum is 3"):
        build_seed_rows(cases(), members)


def test_committee_membership_comes_from_the_seed_users() -> None:
    assert COMMITTEE == ["u-amendes", "u-jokafor", "u-lchen"]
