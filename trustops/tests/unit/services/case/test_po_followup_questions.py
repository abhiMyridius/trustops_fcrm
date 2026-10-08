"""The owner reads and answers the analyst's ad-hoc questions (ADR-73) through the
same inbox and answer routes as the round's.

What these tests hold: the owner's inbox lists the ad-hoc questions apart from the
round, by the field's label and never the analyst's anchor; an answer is stored
once with the values and documents it brings; a value that changed freezes the
next submission version (``answer``) and names the changed fields, a value equal
to the one held changes nothing; ``case.question_answered`` is always enqueued, so
Caseflow records every answer; and an ad-hoc answer never closes or touches the
D-16 round.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.activity import po_may_receive
from fcrm_contracts.enums import CaseState, Role
from fcrm_contracts.events.payloads import CaseQuestionAnswered
from fcrm_guardrails.rules.r3_po_no_restricted import check as r3_check

if TYPE_CHECKING:
    from conftest import CaseService

CASE = "FCRM-2026-0151"
OWNER = "u-pnair"
ANALYST = "u-riyer"
LICENCE = "Which licence does the payout partner hold in Mexico?"
LICENCE_NO = "MX-114, issued by the CNBV in 2024."


def events(service: CaseService, type_: str) -> list[dict[str, Any]]:
    return [e.payload for e in service.store.events if e.type == type_]


def stage(service: CaseService, state: CaseState) -> None:
    record = service.store._cases[CASE]
    service.store._cases[CASE] = record.model_copy(update={"state": state})


def answer(service: CaseService, thread_id: str, body: dict[str, Any]) -> Any:
    return service.client.post(
        f"/cases/{CASE}/clarifications/{thread_id}/answer", json=body
    )


def assert_plain(payload: Any) -> None:
    verdict = r3_check(payload, Role.PO)
    assert verdict.ok, verdict.detail


@pytest.fixture
def asked(
    clarification_service: CaseService, act_as: Callable[[Role, str], None]
) -> CaseService:
    """In review, the analyst asked the owner one ad-hoc question about F11; the
    caller is now the owner.
    """
    stage(clarification_service, CaseState.REVIEW)
    response = clarification_service.client.post(
        f"/cases/{CASE}/questions",
        json={"questions": [{"text": LICENCE, "anchor": "CTL-T01 / F11"}]},
    )
    assert response.status_code == 201, response.text
    clarification_service.store.events.clear()
    act_as(Role.PO, OWNER)
    return clarification_service


def the_thread(service: CaseService) -> str:
    [thread] = [t for t in service.views.threads.values() if t.kind == "adhoc"]
    return thread.thread_id


# ── the inbox ───────────────────────────────────────────────────────────────────


def test_the_owner_reads_the_question_apart_from_the_round_by_the_field(
    asked: CaseService,
) -> None:
    response = asked.client.get(f"/cases/{CASE}/inbox")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["rounds"] == []
    assert body["banner"] is None
    [question] = body["questions"]
    assert question["kind"] == "adhoc"
    assert question["round"] == 0
    assert question["field_id"] == "F11"
    assert question["question"] == LICENCE
    assert question["status"] == "open"
    assert "CTL" not in json.dumps(body)
    assert_plain(body)


def test_the_owner_hears_of_the_question_in_plain_words(asked: CaseService) -> None:
    [row] = [
        e
        for e in asked.store.activity.entries
        if e.type == "question.asked" and e.visibility == "po"
    ]

    assert po_may_receive(row.type, row.visibility, row.detail, row.summary)
    assert row.summary == "FCRM has a question for you"


# ── answering ───────────────────────────────────────────────────────────────────


def test_an_answer_in_words_only_is_stored_and_caseflow_records_it(
    asked: CaseService,
) -> None:
    thread_id = the_thread(asked)

    response = answer(asked, thread_id, {"text": "It is licensed by the CNBV."})

    assert response.status_code == 200, response.text
    [question] = response.json()["questions"]
    assert question["status"] == "answered"
    row = asked.views.threads[thread_id]
    assert row.status == "answered"
    assert row.resulting_version is None
    assert row.answer is not None
    assert row.answer.changed_field_ids == []
    [payload] = events(asked, "case.question_answered")
    sent = CaseQuestionAnswered.model_validate(payload)
    assert (sent.thread_id, sent.question_id) == (thread_id, "A1")
    assert sent.changed_field_ids == []
    assert sent.submission_version is None
    assert events(asked, "case.answered") == []
    record = asked.store._cases[CASE]
    assert record.current_submission_v == 1


def test_a_changed_value_freezes_the_next_version_and_names_the_field(
    asked: CaseService,
) -> None:
    thread_id = the_thread(asked)

    response = answer(
        asked,
        thread_id,
        {
            "text": "The licence number is in F11 now.",
            "field_values": [{"field_id": "F11", "value": LICENCE_NO}],
        },
    )

    assert response.status_code == 200, response.text
    field = asked.views.field_values[f"{CASE}:F11"]
    assert (field.value, field.origin) == (LICENCE_NO, "answered")
    [v2] = [s for s in asked.po.submissions.values() if s.version == 2]
    assert v2.reason == "answer"
    assert v2.round is None
    assert v2.changed_field_ids == ["F11"]
    assert v2.field_values["F11"].value == LICENCE_NO
    assert asked.store._cases[CASE].current_submission_v == 2
    row = asked.views.threads[thread_id]
    assert row.resulting_version == 2
    assert row.answer is not None
    assert row.answer.changed_field_ids == ["F11"]
    assert row.answer.values == {"F11": LICENCE_NO}
    [payload] = events(asked, "case.question_answered")
    assert payload["changed_field_ids"] == ["F11"]
    assert payload["submission_version"] == 2


def test_both_feeds_are_told_and_the_owner_s_lines_are_plain(
    asked: CaseService,
) -> None:
    answer(
        asked,
        the_thread(asked),
        {"text": "Updated.", "field_values": [{"field_id": "F11", "value": "MX-1"}]},
    )

    rows = [
        e
        for e in asked.store.activity.entries
        if e.type in ("question.answered", "submission.created")
    ]
    owner = [e for e in rows if e.visibility == "po"]
    staff = [e for e in rows if e.visibility == "analyst"]
    assert [e.summary for e in owner] == [
        "You answered a question from FCRM",
        "Your answer is saved as version 2",
    ]
    assert all(po_may_receive(e.type, e.visibility, e.detail, e.summary) for e in owner)
    assert staff[0].summary == "The owner answered question A1"
    assert staff[1].summary.startswith("The owner's answer to question A1 changed ")
    assert staff[1].summary.endswith(": submission v2 saved")


def test_a_value_equal_to_the_one_held_changes_nothing(
    asked: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    answer(
        asked,
        the_thread(asked),
        {"text": "Set.", "field_values": [{"field_id": "F11", "value": LICENCE_NO}]},
    )
    act_as(Role.ANALYST, ANALYST)
    again = asked.client.post(
        f"/cases/{CASE}/questions",
        json={"questions": [{"text": "Please confirm the licence number."}]},
    )
    [second] = again.json()["thread_ids"]
    asked.store.events.clear()
    act_as(Role.PO, OWNER)

    response = answer(
        asked,
        second,
        {"text": "Same.", "field_values": [{"field_id": "F11", "value": LICENCE_NO}]},
    )

    assert response.status_code == 200, response.text
    [payload] = events(asked, "case.question_answered")
    assert payload["changed_field_ids"] == []
    assert payload["submission_version"] is None
    assert asked.store._cases[CASE].current_submission_v == 2


def test_the_same_answer_again_replays_and_another_is_refused(
    asked: CaseService,
) -> None:
    thread_id = the_thread(asked)
    body = {"text": "It is licensed by the CNBV."}

    first = answer(asked, thread_id, body)
    again = answer(asked, thread_id, body)
    other = answer(asked, thread_id, {"text": "Something else."})

    assert (first.status_code, again.status_code) == (200, 200)
    assert other.status_code == 409
    assert len(events(asked, "case.question_answered")) == 1


def test_an_answer_naming_a_reserved_word_is_refused(asked: CaseService) -> None:
    response = answer(asked, the_thread(asked), {"text": "The score is fine."})

    assert response.status_code == 422
    assert events(asked, "case.question_answered") == []


@pytest.mark.parametrize("state", [CaseState.DECIDE, CaseState.RECOMMEND])
def test_an_answer_is_taken_even_once_the_case_moved_on(
    asked: CaseService, state: CaseState
) -> None:
    """No answer is lost: one that arrives after the pack went to the committee
    is stored and Caseflow is told (it records it and flags the values).
    """
    stage(asked, state)

    response = answer(
        asked,
        the_thread(asked),
        {"text": "Updated.", "field_values": [{"field_id": "F11", "value": "MX-9"}]},
    )

    assert response.status_code == 200, response.text
    assert len(events(asked, "case.question_answered")) == 1


def test_a_closed_case_takes_no_answer(asked: CaseService) -> None:
    stage(asked, CaseState.CLOSED)

    response = answer(asked, the_thread(asked), {"text": "Late."})

    assert response.status_code == 409


# ── kept apart from the D-16 round ──────────────────────────────────────────────


def test_an_adhoc_answer_never_closes_the_round_and_an_open_one_never_holds_it(
    clarification_service: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    client = clarification_service.client
    client.post(f"/cases/{CASE}/questions", json={"questions": [{"text": LICENCE}]})
    client.post(f"/cases/{CASE}/completeness", json={"decision": "ask"})
    stage(clarification_service, CaseState.CLARIFICATION)
    threads = list(clarification_service.views.threads.values())
    adhoc = next(t for t in threads if t.kind == "adhoc")
    in_round = sorted(
        (t for t in threads if t.kind == "round"), key=lambda t: t.question_id
    )
    clarification_service.store.events.clear()
    act_as(Role.PO, OWNER)

    answer(clarification_service, adhoc.thread_id, {"text": "CNBV licence."})
    assert events(clarification_service, "case.answered") == []

    for thread in in_round:
        response = answer(clarification_service, thread.thread_id, {"text": "Yes."})
        assert response.status_code == 200, response.text

    [closed] = events(clarification_service, "case.answered")
    assert sorted(closed["thread_ids"]) == sorted(t.thread_id for t in in_round)
    assert closed["round"] == 1
    [question] = events(clarification_service, "case.question_answered")
    assert question["thread_id"] == adhoc.thread_id
