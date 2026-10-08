"""S1's ad-hoc questions to the owner (ADR-73): ``POST /cases/{id}/questions`` and
the Inbox view that lists them, and the D-120 fix to the round's composer.

What these tests hold: the analyst who holds the case may ask the owner at any
stage from triage to an unlocked recommendation (and in a handoff), never in
DECIDE, on a locked recommendation, before triage or once closed; each question
opens its own ``adhoc`` thread (round 0) and ``case.question_asked`` follows the
threads; a question that would show the owner a reserved term is refused with a
422 that names it; ad-hoc threads are kept apart from the D-16 round, whose
one-round rule and clock pause are unchanged; a repeat while the same question is
open is a replay; and the round can be composed before it is sent.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Optional

import pytest
from fcrm_case.domain.analyst_assessment import clarifications_view
from fcrm_case.domain.clarifications import (
    ADHOC_ROUND,
    ClarificationThreadRow,
    ThreadAnswerRow,
    ask_blocked_reason,
    values_changed_since,
)
from fcrm_case.domain.record import StageFlags
from fcrm_contracts.api.case_analyst_read import ClarificationsView
from fcrm_contracts.api.case_analyst_write import QuestionsResult
from fcrm_contracts.enums import CaseState, Role
from fcrm_contracts.events.payloads import CaseQuestionAsked

if TYPE_CHECKING:
    from conftest import CaseService, EmitProbe

CASE = "FCRM-2026-0151"
QUESTIONS = f"/cases/{CASE}/questions"
COMPLETENESS = f"/cases/{CASE}/completeness"
CLARIFICATIONS = f"/cases/{CASE}/clarifications"
LICENCE = "Which licence does the payout partner hold in Mexico?"
VOLUME = "What volume do you expect in the first year?"

ASKABLE = [
    CaseState.TRIAGE,
    CaseState.SCREENING,
    CaseState.ASSESS,
    CaseState.CRITIC,
    CaseState.COMPLETENESS,
    CaseState.CLARIFICATION,
    CaseState.REVIEW,
    CaseState.RECOMMEND,
    CaseState.HANDOFF,
]


def code(response: Any) -> str:
    return str(response.json()["type"]).rsplit("/", 1)[-1]


def events(service: CaseService, type_: str) -> list[dict[str, Any]]:
    return [e.payload for e in service.store.events if e.type == type_]


def stage(service: CaseService, state: CaseState, *, locked: bool = False) -> None:
    record = service.store._cases[CASE]
    service.store._cases[CASE] = record.model_copy(
        update={"state": state, "stage_flags": StageFlags(locked=locked)}
    )


def ask(service: CaseService, *questions: dict[str, Any]) -> Any:
    return service.client.post(QUESTIONS, json={"questions": list(questions)})


def q(text: str, anchor: Optional[str] = None) -> dict[str, Any]:
    return {"text": text, "anchor": anchor}


def adhoc(service: CaseService) -> list[ClarificationThreadRow]:
    rows = [t for t in service.views.threads.values() if t.kind == "adhoc"]
    return sorted(rows, key=lambda t: t.question_id)


def inbox(service: CaseService) -> ClarificationsView:
    response = service.client.get(CLARIFICATIONS)
    assert response.status_code == 200, response.text
    return ClarificationsView.model_validate(response.json())


# ── who may ask ─────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("role", [Role.PO, Role.COMMITTEE, Role.OBSERVER, Role.SERVICE])
def test_only_the_analyst_asks(
    questions_service: CaseService,
    act_as: Callable[[Role, str], None],
    role: Role,
) -> None:
    act_as(role, "someone")

    response = ask(questions_service, q(LICENCE))

    assert response.status_code == 403
    assert questions_service.views.threads == {}


def test_an_analyst_who_does_not_hold_the_case_may_not_ask(
    questions_service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    act_as(Role.ANALYST, "u-mshah")

    assert ask(questions_service, q(LICENCE)).status_code == 403


# ── asking ──────────────────────────────────────────────────────────────────────


def test_each_question_opens_its_own_adhoc_thread(
    questions_service: CaseService,
) -> None:
    stage(questions_service, CaseState.REVIEW)

    response = ask(questions_service, q(LICENCE, "F11"), q(VOLUME))

    assert response.status_code == 201, response.text
    result = QuestionsResult.model_validate(response.json())
    assert (result.questions, result.replayed) == (2, False)
    first, second = adhoc(questions_service)
    assert [first.question_id, second.question_id] == ["A1", "A2"]
    assert {first.kind, second.kind} == {"adhoc"}
    assert {first.round, second.round} == {ADHOC_ROUND}
    assert result.thread_ids == [first.thread_id, second.thread_id]
    assert first.field_ids == ["F11"]
    assert first.field_label not in ("", "Your submission")
    assert second.field_label == "Your submission"
    assert {first.status, second.status} == {"awaiting_response"}


def test_caseflow_hears_the_questions_after_the_threads_exist_and_no_gate_moves(
    questions_service: CaseService, emit_probe: EmitProbe
) -> None:
    ask(questions_service, q(LICENCE), q(VOLUME))

    assert [e.type for e in questions_service.store.events] == ["case.question_asked"]
    assert emit_probe.threads == [2]
    [payload] = events(questions_service, "case.question_asked")
    asked = CaseQuestionAsked.model_validate(payload)
    assert asked.question_count == 2
    assert sorted(asked.thread_ids) == sorted(questions_service.views.threads)
    assert questions_service.store.events[0].run_id == "run_1"


def test_both_feeds_hear_of_it_and_the_clock_keeps_running(
    questions_service: CaseService,
) -> None:
    ask(questions_service, q(LICENCE))

    entries = [
        e
        for e in questions_service.store.activity.entries
        if e.type == "question.asked"
    ]
    assert sorted(e.visibility for e in entries) == ["analyst", "po"]
    owner = next(e for e in entries if e.visibility == "po")
    staff = next(e for e in entries if e.visibility == "analyst")
    assert owner.summary == "FCRM has a question for you"
    assert "clock keeps running" in staff.summary
    assert "(A1)" in staff.summary


@pytest.mark.parametrize("state", ASKABLE)
def test_a_question_may_be_asked_at_every_stage_the_analyst_holds_the_case(
    questions_service: CaseService, state: CaseState
) -> None:
    stage(questions_service, state)

    response = ask(questions_service, q(LICENCE))

    assert response.status_code == 201, (state, response.text)


@pytest.mark.parametrize(
    ("state", "locked"),
    [
        (CaseState.DECIDE, True),
        (CaseState.RECOMMEND, True),
        (CaseState.INTAKE, False),
        (CaseState.REVIEW_AND_SUBMIT, False),
    ],
)
def test_no_question_with_the_committee_on_a_locked_case_or_before_triage(
    questions_service: CaseService, state: CaseState, locked: bool
) -> None:
    stage(questions_service, state, locked=locked)

    response = ask(questions_service, q(LICENCE))

    assert response.status_code == 409
    assert code(response) == "wrong_stage"
    assert questions_service.views.threads == {}
    assert questions_service.store.events == []


def test_a_closed_case_takes_no_question(questions_service: CaseService) -> None:
    stage(questions_service, CaseState.CLOSED)

    response = ask(questions_service, q(LICENCE))

    assert response.status_code == 409
    assert code(response) == "case_closed"


@pytest.mark.parametrize(
    ("text", "word"),
    [
        ("What rating would you give the partner?", "rating"),
        ("Your band is high; why?", "band"),
        ("Explain the score.", "score"),
    ],
)
def test_a_question_that_would_show_the_owner_a_reserved_term_is_refused(
    questions_service: CaseService, text: str, word: str
) -> None:
    response = ask(questions_service, q(LICENCE), q(text))

    assert response.status_code == 422
    body = response.json()
    assert "Question 2" in body["detail"]
    assert word in body["detail"]
    assert questions_service.views.threads == {}
    assert questions_service.store.events == []


def test_an_anchor_that_is_not_the_cases_is_refused(
    questions_service: CaseService,
) -> None:
    response = ask(questions_service, q(LICENCE, "F99"))

    assert response.status_code == 422
    assert questions_service.views.threads == {}


def test_bad_bodies_are_refused(questions_service: CaseService) -> None:
    client = questions_service.client

    assert ask(questions_service, q(LICENCE), q(LICENCE)).status_code == 422
    assert client.post(QUESTIONS, json={"questions": []}).status_code == 422
    assert client.post(QUESTIONS, json={"questions": [q("   ")]}).status_code == 422
    eleven = [q(f"Question number {n}?") for n in range(11)]
    assert client.post(QUESTIONS, json={"questions": eleven}).status_code == 422
    assert (
        client.post(QUESTIONS, json={"questions": [q(LICENCE)], "x": 1}).status_code
        == 422
    )


def test_the_same_open_question_again_is_a_replay(
    questions_service: CaseService,
) -> None:
    first = ask(questions_service, q(LICENCE))
    again = ask(questions_service, q(LICENCE))

    assert again.status_code == 201
    assert again.json()["replayed"] is True
    assert again.json()["thread_ids"] == first.json()["thread_ids"]
    assert len(questions_service.views.threads) == 1
    assert len(events(questions_service, "case.question_asked")) == 1


def test_a_new_question_beside_an_open_one_opens_only_the_new_one(
    questions_service: CaseService,
) -> None:
    ask(questions_service, q(LICENCE))

    response = ask(questions_service, q(LICENCE), q(VOLUME))

    assert response.json()["replayed"] is False
    assert [t.question_id for t in adhoc(questions_service)] == ["A1", "A2"]
    [_, second] = events(questions_service, "case.question_asked")
    assert second["question_count"] == 1


def test_a_question_may_be_asked_again_once_answered(
    questions_service: CaseService,
) -> None:
    ask(questions_service, q(LICENCE))
    [thread] = adhoc(questions_service)
    questions_service.views.threads[thread.thread_id] = thread.model_copy(
        update={"status": "answered"}
    )

    response = ask(questions_service, q(LICENCE))

    assert response.json()["replayed"] is False
    assert [t.question_id for t in adhoc(questions_service)] == ["A1", "A2"]


def test_an_answered_adhoc_question_is_resolved_like_a_round_one(
    questions_service: CaseService,
) -> None:
    ask(questions_service, q(LICENCE))
    [thread] = adhoc(questions_service)
    questions_service.views.threads[thread.thread_id] = thread.model_copy(
        update={"status": "answered"}
    )

    response = questions_service.client.post(
        f"{CLARIFICATIONS}/{thread.thread_id}/resolve", json={}
    )

    assert response.status_code == 200
    assert questions_service.views.threads[thread.thread_id].status == "resolved"
    resolved = [
        e
        for e in questions_service.store.activity.entries
        if e.type == "thread.resolved"
    ]
    assert resolved[-1].summary.endswith("resolved question A1")


# ── kept apart from the D-16 round ──────────────────────────────────────────────


def test_an_adhoc_question_leaves_the_round_to_be_asked_once(
    questions_service: CaseService,
) -> None:
    ask(questions_service, q(LICENCE))

    asked = questions_service.client.post(COMPLETENESS, json={"decision": "ask"})
    replay = questions_service.client.post(COMPLETENESS, json={"decision": "ask"})
    second = questions_service.client.post(
        CLARIFICATIONS,
        json={"questions": [{"id": "Q9", "text": VOLUME, "anchor": None}]},
    )

    assert asked.status_code == 202, asked.text
    assert asked.json()["round"] == 1
    round_ids = {t.thread_id for t in questions_service.views.threads.values()} - {
        t.thread_id for t in adhoc(questions_service)
    }
    assert set(asked.json()["thread_ids"]) == round_ids
    assert replay.json()["replayed"] is True
    assert set(replay.json()["thread_ids"]) == round_ids
    assert second.status_code == 409
    assert code(second) == "round_already_sent"


def test_only_the_round_pauses_the_clock_and_lists_as_a_round(
    questions_service: CaseService,
) -> None:
    ask(questions_service, q(LICENCE))

    view = inbox(questions_service)

    assert view.clock_paused is False
    assert view.rounds == []
    [question] = view.questions
    assert (question.kind, question.round, question.status) == ("adhoc", 0, "open")
    # the completeness gate is still open: the round can still be composed
    assert view.can_send is True


def test_the_round_s_composer_shows_until_its_send_decides_the_gate(
    questions_service: CaseService,
) -> None:
    """D-120: the composer was gated on a decision that created the threads in
    the same write, so it never showed. It now shows while the gate is open, and
    its send (with the questions kept and edited) is what decides the gate.
    """
    before = inbox(questions_service)
    assert before.can_send is True
    assert [d.id for d in before.drafted if d.keep] == ["Q1", "Q2"]

    sent = questions_service.client.post(
        CLARIFICATIONS,
        json={
            "questions": [
                {"id": "Q1", "text": "Is the partner licensed?", "anchor": "F11"},
                {"id": "Q2", "text": "dropped", "anchor": "F21", "keep": False},
            ]
        },
    )

    assert sent.status_code == 201, sent.text
    assert sent.json()["questions"] == 1
    after = inbox(questions_service)
    assert after.can_send is False
    [only] = after.rounds[0].threads
    assert only.question == "Is the partner licensed?"
    assert after.clock_paused is True
    [decided] = events(questions_service, "gate.decided")
    assert decided["decision"] == "ask"


@pytest.mark.parametrize("state", [CaseState.REVIEW, CaseState.TRIAGE])
def test_the_composer_is_shown_at_the_completeness_gate_only(
    questions_service: CaseService, state: CaseState
) -> None:
    stage(questions_service, state)

    assert inbox(questions_service).can_send is False


# ── the inbox view ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("state", "locked", "can_ask"),
    [
        (CaseState.REVIEW, False, True),
        (CaseState.RECOMMEND, False, True),
        (CaseState.RECOMMEND, True, False),
        (CaseState.DECIDE, True, False),
    ],
)
def test_the_view_says_whether_a_question_may_be_asked_and_why_not(
    questions_service: CaseService, state: CaseState, locked: bool, can_ask: bool
) -> None:
    stage(questions_service, state, locked=locked)

    view = inbox(questions_service)

    assert view.can_ask is can_ask
    assert (view.ask_blocked_reason is None) is can_ask


def test_ask_blocked_reason_reads_the_case(stored_case: Any) -> None:
    held = stored_case.model_copy(update={"analyst_id": "u-riyer"})

    assert (
        ask_blocked_reason(held.model_copy(update={"state": CaseState.REVIEW})) is None
    )
    assert "closed" in str(
        ask_blocked_reason(held.model_copy(update={"state": CaseState.CLOSED}))
    )
    assert "locked" in str(
        ask_blocked_reason(
            held.model_copy(
                update={
                    "state": CaseState.RECOMMEND,
                    "stage_flags": StageFlags(locked=True),
                }
            )
        )
    )
    assert "analyst" in str(
        ask_blocked_reason(
            stored_case.model_copy(
                update={"state": CaseState.TRIAGE, "analyst_id": None}
            )
        )
    )


def _answered(
    question_id: str, at: datetime, values: dict[str, str]
) -> ClarificationThreadRow:
    return ClarificationThreadRow(
        thread_id=f"thr_{question_id}",
        case_id=CASE,
        kind="adhoc",
        round=ADHOC_ROUND,
        question_id=question_id,
        text="?",
        field_label="Your submission",
        status="answered",
        opened_at=datetime(2026, 10, 5, 9, tzinfo=UTC),
        opened_by="u-riyer",
        answer=ThreadAnswerRow(
            text="answer",
            at=at,
            by="u-pnair",
            field_ids=list(values),
            values=values,
            changed_field_ids=list(values),
        ),
        resulting_version=3,
    )


def test_values_changed_since_the_agents_scored_are_the_ones_after_the_score() -> None:
    scored = datetime(2026, 10, 5, 12, tzinfo=UTC)
    before = _answered("A1", datetime(2026, 10, 5, 11, tzinfo=UTC), {"F11": "old"})
    after = _answered("A2", datetime(2026, 10, 5, 13, tzinfo=UTC), {"F11": "MX-114"})
    later = _answered("A3", datetime(2026, 10, 5, 14, tzinfo=UTC), {"F21": "38"})

    assert values_changed_since([later, before, after], scored) == {
        "F11": "MX-114",
        "F21": "38",
    }
    assert values_changed_since([before, after], None) == {}


def test_the_inbox_names_the_changed_values_with_their_labels(
    questions_service: CaseService,
) -> None:
    record = questions_service.store._cases[CASE]
    thread = _answered("A1", datetime(2026, 10, 5, 13, tzinfo=UTC), {"F11": "MX-114"})

    view = clarifications_view(
        record, None, None, [thread], datetime(2026, 10, 5, 12, tzinfo=UTC)
    )

    [changed] = view.values_changed
    assert (changed.field_id, changed.value) == ("F11", "MX-114")
    assert changed.label != "F11"
    [question] = view.questions
    assert question.answer is not None
    assert question.answer.submission_version == 3
    assert [f.field_id for f in question.answer.fields] == ["F11"]
