"""S5's projections: rows to the web's shapes, and above all who may read which
vote. ``votes_view`` is the only place a stored vote becomes a choice a client
can read.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fcrm_common.time import utcnow
from fcrm_contracts.api.decision import OutcomeSet, ReleaseChecks
from fcrm_contracts.api.identity import RosterUser
from fcrm_contracts.enums import Outcome, Role, VoteOption
from fcrm_decision.rows import (
    AnswerRow,
    OutcomeRow,
    PackRow,
    QueueEntry,
    RecordedOutcome,
    ThreadRow,
    VoteRow,
)
from fcrm_decision.views import (
    addendum,
    case_outcome_view,
    queue_item,
    thread_view,
    votes_view,
)

ROSTER = [
    RosterUser(id="u-amendes", name="Ana Mendes", roles=[Role.COMMITTEE]),
    RosterUser(id="u-jokafor", name="Joseph Okafor", roles=[Role.COMMITTEE]),
    RosterUser(id="u-lchen", name="Lin Chen", roles=[Role.COMMITTEE]),
]
NOW = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def vote(member: str, option: str, minutes: int = 0) -> VoteRow:
    return VoteRow(
        pack_id="pack_1",
        round=1,
        member_id=member,
        option=VoteOption(option),
        rationale=f"{member} on {option}",
        cast_at=NOW + timedelta(minutes=minutes),
    )


def result(outcome: str | None, *, no_majority: bool = False) -> OutcomeRow:
    return OutcomeRow(
        outcome=OutcomeSet(
            case_id="FCRM-2026-0151",
            pack_id="pack_1",
            round=1,
            outcome=Outcome(outcome) if outcome else None,
            no_majority=no_majority,
        ),
        decided_at=NOW,
    )


def view(votes: list[VoteRow], outcome: OutcomeRow | None, viewer: str | None) -> dict:
    return votes_view(
        pack_id="pack_1",
        round_=1,
        votes=votes,
        outcome=outcome,
        roster=ROSTER,
        viewer_id=viewer,
    ).model_dump(mode="json")


# ── hidden until revealed ────────────────────────────────────────────────


@pytest.mark.parametrize("viewer", [None, "u-amendes", "u-jokafor", "u-lchen"])
def test_votes_view_before_the_reveal_never_shows_a_peers_choice(
    viewer: str | None,
) -> None:
    votes = [vote("u-jokafor", "reject"), vote("u-amendes", "defer", 1)]

    body = view(votes, None, viewer)

    assert body["revealed"] is False
    assert body["cast_count"] == 2
    assert body["tally"] is None
    assert body["outcome"] is None
    for member in body["members"]:
        mine = member["is_you"]
        voted = member["status"] == "voted"
        assert voted == (member["name"] in ("Joseph Okafor", "Ana Mendes"))
        if not mine:
            assert member["choice"] is None
            assert member["rationale"] is None
            assert member["cast_at"] is None


def test_votes_view_the_caller_always_sees_their_own_vote() -> None:
    votes = [vote("u-jokafor", "reject")]
    body = view(votes, None, "u-jokafor")
    me = next(m for m in body["members"] if m["is_you"])
    assert me["name"] == "Joseph Okafor"
    assert (me["choice"], me["rationale"]) == ("reject", "u-jokafor on reject")
    assert body["mine"]["choice"] == "reject"


def test_votes_view_a_viewer_who_has_not_voted_has_no_mine() -> None:
    body = view([vote("u-jokafor", "reject")], None, "u-lchen")
    assert body["mine"] is None
    assert next(m for m in body["members"] if m["is_you"])["status"] == "not_voted"


def test_votes_view_the_analyst_owns_no_seat() -> None:
    body = view([vote("u-jokafor", "reject")], None, None)
    assert body["mine"] is None
    assert not any(m["is_you"] for m in body["members"])


def test_votes_view_a_tallied_round_shows_everything_together() -> None:
    votes = [
        vote("u-amendes", "approve_with_conditions"),
        vote("u-jokafor", "approve_with_conditions", 1),
        vote("u-lchen", "defer", 2),
    ]

    body = view(votes, result("approve_with_conditions"), None)

    assert body["revealed"] is True
    assert body["tally"] == {"approve_with_conditions": 2, "defer": 1}
    assert body["outcome"] == "approve_with_conditions"
    assert body["outcome_label"] == "Approved with conditions"
    assert [m["choice"] for m in body["members"]] == [
        "approve_with_conditions",
        "approve_with_conditions",
        "defer",
    ]
    assert all(m["rationale"] and m["cast_at"] for m in body["members"])


def test_votes_view_a_split_is_revealed_as_no_majority_not_as_an_outcome() -> None:
    votes = [
        vote("u-amendes", "approve"),
        vote("u-jokafor", "defer"),
        vote("u-lchen", "reject"),
    ]

    body = view(votes, result(None, no_majority=True), None)

    assert body["revealed"] is True
    assert body["outcome"] == "no_majority"
    assert body["outcome_label"] == "No majority · re-vote requested (ADR-08)"
    assert body["tally"] == {"approve": 1, "defer": 1, "reject": 1}


def test_votes_view_a_roster_member_who_has_not_voted_is_still_listed() -> None:
    body = view([], None, None)
    assert [m["status"] for m in body["members"]] == ["not_voted"] * 3
    assert body["quorum"] == 3


# ── queue, threads, outcomes ─────────────────────────────────────────────


def entry(**changes: object) -> QueueEntry:
    base: dict[str, object] = {
        "pack_id": "pack_1",
        "case_id": "FCRM-2026-0151",
        "version": 1,
        "released_at": NOW,
        "title": "Remittances",
        "analyst_name": "Rahul Iyer",
        "change_types": ["new_product"],
        "band": 3,
        "band_label": "Elevated",
        "voters": ["u-jokafor"],
    }
    return QueueEntry.model_validate({**base, **changes})


def test_queue_item_reports_the_callers_own_status_and_the_count() -> None:
    mine = queue_item(entry(), "u-jokafor")
    other = queue_item(entry(), "u-lchen")
    assert (mine.your_vote, mine.cast_count) == ("voted", 1)
    assert (other.your_vote, other.cast_count) == ("not_voted", 1)
    assert (mine.status, mine.outcome_label, mine.quorum) == ("open", None, 3)


def test_queue_item_a_decided_pack_carries_its_label() -> None:
    item = queue_item(entry(decided=True, outcome="reject"), "u-lchen")
    assert (item.status, item.outcome_label) == ("decided", "Rejected")


def test_queue_item_refuses_a_band_outside_one_to_four() -> None:
    with pytest.raises(ValueError, match="band 9"):
        queue_item(entry(band=9), "u-lchen")


def test_queue_entry_from_a_pack_reads_the_frozen_content() -> None:
    pack = PackRow(
        pack_id="pack_1",
        case_id="C",
        version=2,
        content={
            "title": "T",
            "analyst_name": "A",
            "change_types": ["new_feature"],
            "score": {"band": 2, "band_label": "Moderate"},
        },
        checks=ReleaseChecks(
            locked=True,
            all_items_reviewed=True,
            boundary_and_band_acknowledged=True,
            business_summary_present=True,
        ),
        released_at=NOW,
    )
    built = QueueEntry.from_pack(pack, ["u-x"], None)
    assert (built.title, built.band, built.band_label) == ("T", 2, "Moderate")
    assert built.voters == ["u-x"]
    unreleased = pack.model_copy(update={"released_at": None})
    with pytest.raises(ValueError, match="never released"):
        QueueEntry.from_pack(unreleased, [], None)


def test_thread_view_serialises_from_under_its_web_key_and_nests_the_answer() -> None:
    thread = ThreadRow(
        thread_id="thr_1",
        pack_id="pack_1",
        case_id="C",
        asked_by="u-amendes",
        asked_by_name="Ana Mendes",
        text="Why?",
        created_at=NOW,
        answer=AnswerRow(
            by="u-riyer",
            by_name="Rahul Iyer",
            text="Because.",
            at=NOW,
            addendum_id="addm_1",
            addendum_no=1,
            numbers_changed=False,
        ),
    )
    body = thread_view(thread).model_dump(mode="json", by_alias=True)
    assert body["from"] == "Ana Mendes"
    assert body["answer"]["addendum_no"] == 1
    assert body["answer"]["by"] == "Rahul Iyer"
    unanswered = thread_view(thread.model_copy(update={"answer": None}))
    assert unanswered.answer is None


def test_an_unanswered_thread_is_not_an_addendum() -> None:
    thread = ThreadRow(
        thread_id="thr_1",
        pack_id="pack_1",
        case_id="C",
        asked_by="u",
        asked_by_name="U",
        text="?",
        created_at=utcnow(),
    )
    with pytest.raises(ValueError, match="no answer"):
        addendum(thread)


def test_case_outcome_view_a_split_decided_nothing() -> None:
    with pytest.raises(ValueError, match="decided nothing"):
        case_outcome_view(result(None, no_majority=True))


def test_case_outcome_view_carries_what_was_recorded() -> None:
    row = result("approve").model_copy(
        update={
            "recorded": RecordedOutcome(
                decided_by="committee",
                plain_summary="Approved.",
                recorded_at=NOW,
            )
        }
    )
    body = case_outcome_view(row)
    assert (body.label, body.mode, body.recorded) == ("Approved", "committee", True)
    assert body.plain_summary == "Approved."
    bare = case_outcome_view(result("approve"))
    assert bare.recorded is False
    assert bare.conditions == []
