"""S1's ad-hoc questions name a field and a control apart (ADR-73):
``POST /cases/{id}/questions`` takes ``field_id`` and ``control_id`` beside the
older ``anchor``.

What these tests hold: the thread's anchor is the field if one is named, else the
control, else the older ``anchor`` (which behaves exactly as before); the control
is kept on the thread and shown on the analyst's Inbox only; a field must be one
of the case's required fields and a control one of the case's controls; repeats
are told apart by field and control; and the PO fence holds: the owner's view of
a question about a control carries no control id, under any key (ADR-32, the
``test_bb_02_po_no_restricted_fields`` pattern).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Optional

import pytest
from fcrm_case.domain.clarifications import GENERAL_LABEL, ClarificationThreadRow
from fcrm_case.po_followup_views import thread_po
from fcrm_contracts.api.case_analyst_read import ClarificationsView
from fcrm_contracts.api.case_analyst_write import AdhocQuestion
from fcrm_contracts.enums import CaseState, Role
from fcrm_guardrails.rules.r3_po_no_restricted import check as r3_check
from pydantic import ValidationError

if TYPE_CHECKING:
    from conftest import CaseService

CASE = "FCRM-2026-0151"
QUESTIONS = f"/cases/{CASE}/questions"
LICENCE = "Which licence does the payout partner hold in Mexico?"
CONTROL = "CTL-T01"


def stage(service: CaseService, state: CaseState) -> None:
    record = service.store._cases[CASE]
    service.store._cases[CASE] = record.model_copy(update={"state": state})


def ask(service: CaseService, *questions: dict[str, Any]) -> Any:
    return service.client.post(QUESTIONS, json={"questions": list(questions)})


def q(
    text: str = LICENCE,
    *,
    field_id: Optional[str] = None,
    control_id: Optional[str] = None,
) -> dict[str, Any]:
    return {"text": text, "field_id": field_id, "control_id": control_id}


def adhoc(service: CaseService) -> list[ClarificationThreadRow]:
    rows = [t for t in service.views.threads.values() if t.kind == "adhoc"]
    return sorted(rows, key=lambda t: t.question_id)


def label_of(service: CaseService, field_id: str) -> str:
    record = service.store._cases[CASE]
    return next(d.label for d in record.required_fields if d.field_id == field_id)


def keys_and_strings(payload: Any) -> list[str]:
    """Every key and every string value in ``payload``, at any depth."""
    found: list[str] = []
    if isinstance(payload, dict):
        for key, value in payload.items():
            found.append(str(key))
            found.extend(keys_and_strings(value))
    elif isinstance(payload, list):
        for value in payload:
            found.extend(keys_and_strings(value))
    elif isinstance(payload, str):
        found.append(payload)
    return found


@pytest.fixture
def in_review(questions_service: CaseService) -> CaseService:
    stage(questions_service, CaseState.REVIEW)
    return questions_service


# ── the body ────────────────────────────────────────────────────────────────────


def test_the_body_strips_the_ids_and_picks_the_primary_anchor() -> None:
    both = AdhocQuestion(text=LICENCE, field_id=" F11 ", control_id=f" {CONTROL} ")
    control = AdhocQuestion(text=LICENCE, control_id=CONTROL)
    older = AdhocQuestion(text=LICENCE, anchor=" F11 ")
    blank = AdhocQuestion(text=LICENCE, field_id="  ", control_id="")

    assert (both.field_id, both.control_id, both.primary_anchor) == (
        "F11",
        CONTROL,
        "F11",
    )
    assert control.primary_anchor == CONTROL
    assert (older.primary_anchor, older.control_id) == ("F11", None)
    assert (blank.field_id, blank.control_id, blank.primary_anchor) == (
        None,
        None,
        None,
    )
    with pytest.raises(ValidationError):
        AdhocQuestion(text=LICENCE, control_id="C" * 65)
    with pytest.raises(ValidationError):
        AdhocQuestion(text=LICENCE, field_id="F" * 65)


# ── asking ──────────────────────────────────────────────────────────────────────


def test_a_field_and_a_control_together_anchor_to_the_field_and_keep_the_control(
    in_review: CaseService,
) -> None:
    response = ask(in_review, q(field_id="F11", control_id=CONTROL))

    assert response.status_code == 201, response.text
    [thread] = adhoc(in_review)
    assert thread.anchor == "F11"
    assert thread.control_id == CONTROL
    assert thread.field_ids == ["F11"]
    assert thread.field_label == label_of(in_review, "F11")


def test_a_control_alone_anchors_to_the_control_and_the_owner_reads_a_general_label(
    in_review: CaseService,
) -> None:
    assert ask(in_review, q(control_id=CONTROL)).status_code == 201

    [thread] = adhoc(in_review)
    assert (thread.anchor, thread.control_id) == (CONTROL, CONTROL)
    assert thread.field_ids == []
    assert thread.field_label == GENERAL_LABEL


def test_an_older_client_s_anchor_behaves_as_before(in_review: CaseService) -> None:
    response = in_review.client.post(
        QUESTIONS, json={"questions": [{"text": LICENCE, "anchor": "F11"}]}
    )

    assert response.status_code == 201, response.text
    [thread] = adhoc(in_review)
    assert (thread.anchor, thread.control_id, thread.field_ids) == (
        "F11",
        None,
        ["F11"],
    )


def test_the_analyst_s_inbox_shows_the_control_beside_the_anchor(
    in_review: CaseService,
) -> None:
    ask(in_review, q(field_id="F11", control_id=CONTROL), q("Anything else?"))

    response = in_review.client.get(f"/cases/{CASE}/clarifications")

    assert response.status_code == 200, response.text
    view = ClarificationsView.model_validate(response.json())
    assert [(t.anchor, t.control_id) for t in view.questions] == [
        ("F11", CONTROL),
        ("", None),
    ]


# ── validation ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("question", "message"),
    [
        (q(field_id="F99"), "F99 is not a field of this case."),
        (q(field_id=CONTROL), f"{CONTROL} is not a field of this case."),
        (q(control_id="CTL-ZZ9"), "CTL-ZZ9 is not a control of this case."),
        (q(control_id="F11"), "F11 is not a control of this case."),
        (
            q(field_id="F11", control_id="CTL-ZZ9"),
            "CTL-ZZ9 is not a control of this case.",
        ),
    ],
)
def test_a_field_or_control_that_is_not_the_case_s_is_refused(
    in_review: CaseService, question: dict[str, Any], message: str
) -> None:
    response = ask(in_review, question)

    assert response.status_code == 422, response.text
    assert message in response.json()["detail"]
    assert in_review.views.threads == {}
    assert in_review.store.events == []


def test_repeats_are_told_apart_by_field_and_control(in_review: CaseService) -> None:
    same = q(field_id="F11", control_id=CONTROL)

    assert ask(in_review, same, same).status_code == 422
    first = ask(in_review, same)
    again = ask(in_review, same)
    other = ask(in_review, q(field_id="F11"))

    assert again.json()["replayed"] is True
    assert again.json()["thread_ids"] == first.json()["thread_ids"]
    assert other.json()["replayed"] is False
    assert [(t.anchor, t.control_id) for t in adhoc(in_review)] == [
        ("F11", CONTROL),
        ("F11", None),
    ]


# ── the PO fence (ADR-32) ───────────────────────────────────────────────────────


def test_the_owner_s_view_of_a_question_about_a_control_names_no_control(
    in_review: CaseService, act_as: Callable[[Role, str], None]
) -> None:
    ask(
        in_review,
        q(field_id="F11", control_id=CONTROL),
        q("Is the monitoring in place?", control_id=CONTROL),
    )
    act_as(Role.PO, "u-pnair")

    response = in_review.client.get(f"/cases/{CASE}/inbox")

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["questions"]) == 2
    found = keys_and_strings(body)
    assert "control_id" not in found
    assert not [s for s in found if "CTL" in s]
    assert "CTL" not in json.dumps(body)
    verdict = r3_check(body, Role.PO)
    assert verdict.ok, verdict.detail


def test_the_owner_s_thread_shape_drops_the_control() -> None:
    row = ClarificationThreadRow(
        thread_id="thr_x",
        case_id=CASE,
        kind="adhoc",
        round=0,
        question_id="A1",
        text=LICENCE,
        anchor=CONTROL,
        control_id=CONTROL,
        field_label=GENERAL_LABEL,
        opened_at=datetime(2026, 10, 1, tzinfo=UTC),
        opened_by="u-riyer",
    )

    dumped = thread_po(row).model_dump(mode="json")

    assert "control_id" not in keys_and_strings(dumped)
    assert CONTROL not in json.dumps(dumped)
    assert dumped["field_id"] is None
    assert dumped["field_label"] == GENERAL_LABEL
