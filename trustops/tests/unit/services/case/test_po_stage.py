"""``fcrm_case.domain.po_stage``: how a stored case reads to its owner (the six
stages, the five-step strip, the sentence that says what to do next) and the
Mongo filter behind each stage. The filter and the function must agree for every
state, so a list filtered in the database and a header computed in memory never
disagree.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from fcrm_case.domain.po_stage import (
    NEEDS_RESPONSE_STAGES,
    PO_STAGES,
    CaseSummary,
    has_due_date,
    lifecycle_steps,
    needs_response,
    needs_response_filter,
    next_action,
    po_stage,
    stage_filter,
    stage_label,
)
from fcrm_contracts.enums import CaseState

NOW = datetime(2026, 10, 1, tzinfo=UTC)

#: (state, submitted) -> the PO stage, the whole table.
EXPECTED = {
    (CaseState.INTAKE, False): "draft",
    (CaseState.REVIEW_AND_SUBMIT, False): "draft",
    (CaseState.INTAKE, True): "submitted",
    (CaseState.REVIEW_AND_SUBMIT, True): "submitted",
    (CaseState.TRIAGE, True): "submitted",
    (CaseState.TRIAGE, False): "submitted",
    (CaseState.SCREENING, True): "under_assessment",
    (CaseState.ASSESS, True): "under_assessment",
    (CaseState.CRITIC, True): "under_assessment",
    (CaseState.COMPLETENESS, True): "under_assessment",
    (CaseState.REVIEW, True): "under_assessment",
    (CaseState.RECOMMEND, True): "under_assessment",
    (CaseState.HANDOFF, True): "under_assessment",
    (CaseState.CLARIFICATION, True): "awaiting_your_answer",
    (CaseState.DECIDE, True): "decision",
    (CaseState.CLOSED, True): "closed",
}


def matches(query: dict[str, Any], state: CaseState, submitted: bool) -> bool:
    """Evaluate a ``stage_filter`` fragment against ``(state, submitted)``: the
    handful of operators the filters use, enough to compare them with
    :func:`po_stage` without a database.
    """
    for key, expected in query.items():
        if key == "$or":
            if not any(matches(sub, state, submitted) for sub in expected):
                return False
            continue
        actual = state.value if key == "state" else (NOW if submitted else None)
        if isinstance(expected, dict):
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
        elif actual != expected:
            return False
    return True


def record_at(make_case_record: Any, state: CaseState, submitted: bool) -> Any:
    return make_case_record(state=state, submitted_at=NOW if submitted else None)


@pytest.mark.parametrize(("key", "stage"), list(EXPECTED.items()))
def test_every_state_maps_to_its_stage(
    make_case_record: Any, key: tuple[CaseState, bool], stage: str
) -> None:
    state, submitted = key
    assert po_stage(record_at(make_case_record, state, submitted)) == stage


def test_the_table_covers_every_stage() -> None:
    assert set(EXPECTED.values()) == set(PO_STAGES)


@pytest.mark.parametrize("stage", PO_STAGES)
def test_the_filter_selects_exactly_what_the_function_calls_that_stage(
    make_case_record: Any, stage: str
) -> None:
    query = stage_filter(stage)
    for state in CaseState:
        for submitted in (False, True):
            record = record_at(make_case_record, state, submitted)
            assert matches(query, state, submitted) == (po_stage(record) == stage), (
                f"{stage}: {state.value} submitted={submitted}"
            )


def test_needs_response_is_a_draft_or_a_question(make_case_record: Any) -> None:
    query = needs_response_filter()
    for state in CaseState:
        for submitted in (False, True):
            stage = po_stage(record_at(make_case_record, state, submitted))
            assert matches(query, state, submitted) == (stage in NEEDS_RESPONSE_STAGES)
            assert needs_response(stage) == (stage in NEEDS_RESPONSE_STAGES)


def test_an_unknown_stage_is_refused() -> None:
    with pytest.raises(ValueError, match="not a PO stage"):
        stage_filter("weird")


def test_the_strip_marks_everything_before_the_stage_done() -> None:
    steps = lifecycle_steps("under_assessment")
    assert [s.key for s in steps] == [
        "draft",
        "submitted",
        "under_assessment",
        "decision",
        "closed",
    ]
    assert [s.state for s in steps] == [
        "done",
        "done",
        "current",
        "upcoming",
        "upcoming",
    ]
    # a question to answer is a sub-state of assessment
    assert lifecycle_steps("awaiting_your_answer") == steps
    assert [s.state for s in lifecycle_steps("closed")] == ["done"] * 4 + ["current"]


def test_labels_and_due_dates() -> None:
    assert stage_label("awaiting_your_answer") == "Awaiting your answer"
    assert stage_label("decision") == "Decision pending"
    assert [has_due_date(s) for s in PO_STAGES] == [
        False,
        True,
        True,
        True,
        True,
        False,
    ]


@pytest.mark.parametrize(
    ("summary", "expected"),
    [
        (CaseSummary(), "Upload what you already have"),
        (
            CaseSummary(documents=2, documents_unread=2),
            "The assistant is reading your documents",
        ),
        (CaseSummary(documents=2), "The assistant is reading your documents"),
        (
            CaseSummary(documents=1, documents_unreadable=1),
            "Upload another version of the file that could not be read",
        ),
        (
            CaseSummary(documents=2, intake_ready=True, pending_types=("new_product",)),
            "Confirm the proposed change types",
        ),
        (
            CaseSummary(documents=2, intake_ready=True, blocking_gaps_open=3),
            "Answer 3 blocking questions",
        ),
        (
            CaseSummary(documents=2, intake_ready=True, blocking_gaps_open=1),
            "Answer 1 blocking question",
        ),
        (CaseSummary(documents=2, intake_ready=True), "Review & submit"),
    ],
)
def test_the_draft_next_action_follows_the_screens(
    summary: CaseSummary, expected: str
) -> None:
    assert next_action("draft", summary) == expected


@pytest.mark.parametrize(
    ("stage", "summary", "expected"),
    [
        (
            "awaiting_your_answer",
            CaseSummary(open_threads=2),
            "Answer 2 questions from FCRM",
        ),
        (
            "awaiting_your_answer",
            CaseSummary(open_threads=1),
            "Answer a question from FCRM",
        ),
        ("awaiting_your_answer", CaseSummary(), "Answer a question from FCRM"),
        ("closed", CaseSummary(open_conditions=3), "3 open conditions"),
        ("closed", CaseSummary(open_conditions=1), "1 open condition"),
        ("closed", CaseSummary(), "Closed"),
        ("decision", CaseSummary(), "Waiting for the decision"),
        ("submitted", CaseSummary(), "Nothing needed from you"),
        ("under_assessment", CaseSummary(), "Nothing needed from you"),
    ],
)
def test_the_next_action_after_intake(
    stage: str, summary: CaseSummary, expected: str
) -> None:
    assert next_action(stage, summary) == expected
