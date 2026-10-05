"""The events S5 builds: every one validates through its contract model, is in
the catalogue, and none carries a vote's option before the round is tallied.
"""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_common.events import check_event_type
from fcrm_common.time import utcnow
from fcrm_contracts.api.decision import OutcomeCondition, OutcomeRecord, ReleaseChecks
from fcrm_contracts.enums import Outcome, VoteOption
from fcrm_contracts.events.payloads import (
    CommitteeAnswered,
    CommitteeQuestionAsked,
    OutcomeSetPayload,
    PackReleased,
    PackSuperseded,
    VoteCastPayload,
    VoteNoMajority,
    VoteRerequested,
    VoteTallied,
)
from fcrm_contracts.voting import tally
from fcrm_decision import events
from fcrm_decision.rows import AnswerRow, PackRow, ThreadRow, VoteRow

CASE = "FCRM-2026-0151"
MEMBERS = ("u-jokafor", "u-amendes", "u-lchen")
OPTIONS = ("approve_with_conditions", "approve_with_conditions", "defer")
RATIONALES = tuple(f"{m} explains the position at length." for m in MEMBERS)


@pytest.fixture
def pack() -> PackRow:
    return PackRow(
        pack_id="pack_1",
        case_id=CASE,
        version=1,
        content={},
        content_hash="a" * 64,
        checks=ReleaseChecks(
            locked=True,
            all_items_reviewed=True,
            boundary_and_band_acknowledged=True,
            business_summary_present=True,
        ),
        released_at=utcnow(),
        released_to=list(MEMBERS),
    )


@pytest.fixture
def ballot() -> list[VoteRow]:
    return [
        VoteRow(
            pack_id="pack_1",
            round=1,
            member_id=member,
            option=VoteOption(option),
            rationale=rationale,
            cast_at=utcnow(),
        )
        for member, option, rationale in zip(MEMBERS, OPTIONS, RATIONALES, strict=True)
    ]


def test_pack_released_carries_ids_names_hash_and_the_checks_verdict(
    pack: PackRow,
) -> None:
    event = events.pack_released(pack, ["Joseph Okafor", "Ana Mendes", "Lin Chen"])
    payload = PackReleased.model_validate(event.payload)
    assert event.type == "pack.released"
    assert event.case_id == CASE
    assert payload.released_to == list(MEMBERS)
    assert payload.released_to_names == ["Joseph Okafor", "Ana Mendes", "Lin Chen"]
    assert payload.content_hash == "a" * 64
    assert payload.release_checks_passed is True


def test_vote_cast_says_who_voted_and_how_many_never_what(
    pack: PackRow, ballot: list[VoteRow]
) -> None:
    event = events.vote_cast(pack, ballot[2], 3)
    raw = event.model_dump_json()
    assert event.type == "vote.cast"
    assert VoteCastPayload.model_validate(event.payload).cast_count == 3
    assert set(event.payload) == {
        "pack_id",
        "round",
        "member_id",
        "cast_count",
        "quorum",
    }
    for secret in ("defer", "approve", "reject", *RATIONALES):
        assert secret not in raw


def test_vote_tallied_reveals_every_vote_together_with_the_outcome(
    pack: PackRow, ballot: list[VoteRow]
) -> None:
    result = tally([v.option.value for v in ballot])
    event = events.vote_tallied(pack, 1, result, ballot)
    payload = VoteTallied.model_validate(event.payload)
    assert event.type == "vote.tallied"
    assert payload.outcome is Outcome.APPROVE_WITH_CONDITIONS
    assert payload.vote_outcome is Outcome.APPROVE_WITH_CONDITIONS  # H1's guard key
    assert payload.counts == {"approve_with_conditions": 2, "defer": 1}
    assert {v.member_id: v.vote.value for v in payload.votes} == dict(
        zip(MEMBERS, OPTIONS, strict=True)
    )


def test_vote_no_majority_carries_counts_only(pack: PackRow) -> None:
    result = tally(["approve", "defer", "reject"])
    event = events.vote_no_majority(pack, 1, result)
    payload = VoteNoMajority.model_validate(event.payload)
    assert event.type == "vote.no_majority"
    assert payload.counts == {"approve": 1, "defer": 1, "reject": 1}
    assert set(event.payload) == {"pack_id", "round", "counts"}  # no member, no vote


def test_vote_rerequested_names_the_round_the_version_and_the_members(
    pack: PackRow,
) -> None:
    event = events.vote_rerequested(
        pack,
        version=2,
        round_=1,
        member_ids=MEMBERS[:2],
        reason="a number changed",
    )
    payload = VoteRerequested.model_validate(event.payload)
    assert (payload.version, payload.round) == (2, 1)
    assert payload.member_ids == list(MEMBERS[:2])
    assert payload.reason == "a number changed"


def test_pack_superseded_carries_the_reason_and_the_voided_count(
    pack: PackRow,
) -> None:
    event = events.pack_superseded(pack, "the rating changed", 2)
    payload = PackSuperseded.model_validate(event.payload)
    assert event.type == "pack.superseded"
    assert (payload.version, payload.voided_votes) == (1, 2)


def test_committee_events_round_trip_through_their_payload_models(
    pack: PackRow,
) -> None:
    thread = ThreadRow(
        thread_id="thr_1",
        pack_id="pack_1",
        case_id=CASE,
        asked_by="u-amendes",
        asked_by_name="Ana Mendes",
        text="Why is CTL-T01 rated 4.5?",
        created_at=utcnow(),
    )
    asked = events.committee_question_asked(pack, thread)
    assert CommitteeQuestionAsked.model_validate(asked.payload).member_id == "u-amendes"
    answer = AnswerRow(
        by="u-riyer",
        by_name="Rahul Iyer",
        text="Because the audit predates the corridor.",
        at=utcnow(),
        addendum_id="addm_1",
        addendum_no=1,
        numbers_changed=True,
        unlock_reason="The corridor review lowers the rating.",
    )
    answered = events.committee_answered(pack, thread, answer)
    payload = CommitteeAnswered.model_validate(answered.payload)
    assert payload.numbers_changed
    assert payload.committee_numbers_changed
    assert payload.unlock_reason == "The corridor review lowers the rating."


def test_a_numbers_changed_answer_without_a_reason_cannot_be_announced(
    pack: PackRow,
) -> None:
    thread = ThreadRow(
        thread_id="thr_1",
        pack_id="pack_1",
        case_id=CASE,
        asked_by="u",
        asked_by_name="U",
        text="?",
        created_at=utcnow(),
    )
    answer = AnswerRow(
        by="a",
        by_name="A",
        text="!",
        at=utcnow(),
        addendum_id="addm_1",
        addendum_no=1,
        numbers_changed=True,
    )
    with pytest.raises(ValueError, match="unlock_reason"):
        events.committee_answered(pack, thread, answer)


@pytest.mark.parametrize("mode", ["committee", "delegated"])
def test_outcome_set_carries_the_mode_the_conditions_and_the_reason(mode: str) -> None:
    record = OutcomeRecord(
        case_id=CASE,
        mode=mode,  # type: ignore[arg-type]
        outcome=Outcome.APPROVE_WITH_CONDITIONS,
        pack_id="pack_1" if mode == "committee" else None,
        decided_by="u-riyer",
        reason="Band 2, delegated authority." if mode == "delegated" else None,
        conditions=[OutcomeCondition(id="CND-1", text="Corridor DD review filed")],
    )
    event = events.outcome_set(record, pack_id=record.pack_id)
    payload = OutcomeSetPayload.model_validate(event.payload)
    assert event.type == "outcome.set"
    assert payload.mode == mode
    assert [c.id for c in payload.conditions] == ["CND-1"]
    assert payload.pack_id == record.pack_id


def test_every_event_type_s5_builds_is_in_the_catalogue(
    pack: PackRow, ballot: list[VoteRow]
) -> None:
    built: list[Any] = [
        events.pack_released(pack, []),
        events.vote_cast(pack, ballot[0], 1),
        events.vote_tallied(pack, 1, tally([v.option.value for v in ballot]), ballot),
        events.vote_no_majority(pack, 1, tally(["approve", "defer", "reject"])),
        events.vote_rerequested(pack, version=1, round_=2, member_ids=[], reason="r"),
        events.pack_superseded(pack, "reason text", 0),
    ]
    for event in built:
        check_event_type(event.type)
        assert event.event_id.startswith("evt_")
        assert event.case_id == CASE
