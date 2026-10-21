"""``fcrm_case.po_views``: stored rows to the owner's response models. The figures
(percentages, sources, stage sentences) are the server's; the client does no
arithmetic.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional

import pytest
from fcrm_case.domain.activity import ActivityEntry
from fcrm_case.domain.po_stage import CaseSummary
from fcrm_case.domain.views import (
    DocumentRow,
    FieldHistoryEntry,
    FieldSource,
    FieldValueRow,
)
from fcrm_case.fieldset_provider import active_field_set
from fcrm_case.po_views import (
    activity_event,
    assistant_status,
    blocked_reason,
    blocking_questions_open,
    case_po,
    completion_for,
    document_out,
    effective_required,
    guard_line,
    list_item,
    percent,
    review_view,
    source_ref,
)
from fcrm_contracts.api.case_internal import UiActor
from fcrm_contracts.enums import ChangeType

NOW = datetime(2026, 10, 1, 9, tzinfo=UTC)


@pytest.mark.parametrize(
    ("filled", "required", "pct"),
    [
        (0, 18, 0),
        (9, 18, 50),
        (12, 18, 67),
        (15, 18, 83),
        (18, 18, 100),
        (1, 8, 13),  # 12.5 rounds up, not to even
        (3, 8, 38),  # 37.5
        (1, 3, 33),
        (2, 3, 67),
        (0, 0, 0),
    ],
)
def test_percent_is_filled_over_required_rounded_half_up(
    filled: int, required: int, pct: int
) -> None:
    assert percent(filled, required) == pct


def doc(**overrides: Any) -> DocumentRow:
    base: dict[str, Any] = {
        "doc_id": "doc_1",
        "case_id": "FCRM-2026-0151",
        "name": "business_case.pdf",
        "status": "stored",
        "bucket": "uploads",
        "key": "k",
        "size_bytes": 2_097_152,
        "content_type": "application/pdf",
        "uploaded_at": NOW,
    }
    return DocumentRow(**{**base, **overrides})


def test_a_document_span_reads_as_name_page_and_line() -> None:
    ref = source_ref(
        FieldSource(doc_id="doc_1", page=4, line_start=12, line_end=14),
        {"doc_1": doc()},
    )
    assert ref is not None
    assert ref.model_dump(exclude_none=True) == {
        "label": "business_case.pdf p.4 l.12",
        "document": "business_case.pdf",
        "doc_id": "doc_1",
        "page": 4,
        "line": 12,
    }


def test_a_span_in_an_unknown_document_still_reads_as_page_and_line() -> None:
    ref = source_ref(FieldSource(doc_id="gone", page=1, line_start=2), {})
    assert ref is not None
    assert ref.label == "p.1 l.2"
    assert ref.document is None


def test_a_label_only_source_leaves_the_pointers_out_of_the_json() -> None:
    ref = source_ref(FieldSource(label="reused · FCRM-2026-0118"), {})
    assert ref is not None
    assert ref.model_dump(mode="json") == {"label": "reused · FCRM-2026-0118"}


def test_no_source_is_none() -> None:
    assert source_ref(None, {}) is None
    assert source_ref(FieldSource(), {}) is None


@pytest.mark.parametrize(
    ("guard", "status", "expected"),
    [
        (None, "stored", None),
        (
            {"status": "clean", "pii_total": 0},
            "extracted",
            "Scanned · clean · no personal data found",
        ),
        (
            {"status": "masked", "pii_total": 3},
            "extracted",
            "Scanned · 3 personal-data fields masked before any AI read it",
        ),
        (
            {"status": "masked", "pii_total": 1},
            "extracted",
            "Scanned · 1 personal-data field masked before any AI read it",
        ),
        (
            {"status": "flagged", "pii_total": 0},
            "extracted",
            "Scanned · flagged for the analyst · the text was passed on",
        ),
        (None, "failed", "Could not be read. Upload another version."),
    ],
)
def test_the_guard_line_is_counts_and_a_verdict_only(
    guard: Optional[dict[str, Any]], status: str, expected: Optional[str]
) -> None:
    assert guard_line(doc(status=status, guard1=guard)) == expected


def test_the_quarantine_line_tells_the_owner_to_upload_another_version() -> None:
    line = guard_line(doc(status="quarantined", guard1={"status": "quarantined"}))
    assert line is not None
    assert "not read" in line
    assert "another version" in line


@pytest.mark.parametrize(
    ("status", "shown"),
    [
        ("stored", "stored"),
        ("extracted", "read"),
        ("quarantined", "scanned"),
        ("failed", "stored"),
    ],
)
def test_the_document_status_the_web_knows(status: str, shown: str) -> None:
    out = document_out(doc(status=status, pages=14, raised_in_round=1))
    assert out.status == shown
    assert out.pages == 14
    assert out.added_in == "v2"
    assert out.size_mb == 2.0


def test_the_unread_document_has_zero_pages_and_no_version() -> None:
    out = document_out(doc())
    assert (out.pages, out.added_in, out.guard_line) == (0, None, None)


def test_the_assistant_line_follows_the_documents() -> None:
    assert assistant_status([], None, []).state == "waiting"
    reading = assistant_status([doc(), doc(doc_id="d2", name="b.pdf")], None, [])
    assert (reading.state, reading.line) == (
        "reading",
        "Reading business_case.pdf and 1 more",
    )
    extracted = [doc(status="extracted")]
    assert (
        assistant_status(extracted, None, []).state == "reading"
    )  # agents still running
    failed = assistant_status([doc(status="failed")], None, [])
    assert failed.state == "done"
    assert "could be read" in failed.line


@pytest.mark.parametrize(
    ("filled", "required", "threshold", "message"),
    [
        (15, 18, 80, None),
        (9, 18, 80, "Completion 50% is below 80%: 6 more field(s) needed."),
        (14, 18, 80, "Completion 78% is below 80%: 1 more field(s) needed."),
    ],
)
def test_the_blocked_reason_is_the_completion_alone(
    filled: int, required: int, threshold: int, message: Optional[str]
) -> None:
    from fcrm_contracts.api.case_po import Completion

    completion = Completion(
        filled=filled,
        required=required,
        pct=percent(filled, required),
        threshold_pct=threshold,
        always_required_filled=6,
        always_required_total=6,
    )
    assert blocked_reason(completion) == message


def test_only_confirmed_types_add_to_the_required_set(
    make_case_record: Any,
) -> None:
    field_set = active_field_set()
    assert len(effective_required(make_case_record(), field_set)) == 6
    confirmed = make_case_record(change_types=[ChangeType.NEW_PRODUCT])
    assert len(effective_required(confirmed, field_set)) == 6 + 5
    both = make_case_record(
        change_types=[ChangeType.NEW_PRODUCT, ChangeType.NEW_GEOGRAPHY]
    )
    assert len(effective_required(both, field_set)) == 6 + 5 + 4


def test_a_pending_type_adds_nothing_to_the_header(make_case_record: Any) -> None:
    field_set = active_field_set()
    summary = CaseSummary(
        documents=1,
        intake_ready=True,
        pending_types=("new_geography", "new_product"),
        filled_field_ids=frozenset({"F01", "F02", "F03", "F04", "F05", "F06", "F31"}),
    )
    pending = completion_for(make_case_record(), summary, field_set)
    assert (pending.filled, pending.required, pending.pct) == (6, 6, 100)
    confirmed = completion_for(
        make_case_record(change_types=[ChangeType.NEW_PRODUCT]),
        CaseSummary(
            documents=1,
            intake_ready=True,
            pending_types=("new_geography",),
            filled_field_ids=summary.filled_field_ids,
        ),
        field_set,
    )
    # the kept F31 counts the moment new_product is confirmed
    assert (confirmed.filled, confirmed.required) == (7, 11)


def test_a_draft_header_and_row_agree(make_case_record: Any) -> None:
    field_set = active_field_set()
    record = make_case_record()
    summary = CaseSummary(
        documents=1, intake_ready=True, filled_field_ids=frozenset({"F01", "F02"})
    )
    header = case_po(record, summary, field_set)
    row = list_item(record, summary, field_set)
    assert header.completion.pct == row.completion_pct == 33
    # the four empty core fields are asked as blocking questions
    assert header.next_action == row.next_action == "Answer 4 blocking questions"
    assert header.created == "2026-10-01"
    assert header.submitted is None
    assert row.due is None


def test_the_blocking_count_follows_the_confirmed_types(
    make_case_record: Any,
) -> None:
    field_set = active_field_set()
    core = frozenset({"F01", "F02", "F03", "F04", "F05", "F06"})
    # q on F10 (vendor_onboarding, pending) and F21 (new_geography, confirmed)
    summary = CaseSummary(
        documents=1,
        intake_ready=True,
        filled_field_ids=core,
        pending_types=("vendor_onboarding",),
        open_gaps=(("F10", True), ("F21", True), ("F22", False), ("", True)),
    )
    record = make_case_record(change_types=[ChangeType.NEW_GEOGRAPHY])
    # F21 and the question tied to no field; F10 is hidden, F22 optional
    assert blocking_questions_open(record, summary, field_set) == 2
    missing_core = CaseSummary(
        documents=1, intake_ready=True, filled_field_ids=core - {"F03"}
    )
    assert blocking_questions_open(make_case_record(), missing_core, field_set) == 1


def test_a_submitted_row_has_a_due_date_two_business_days_on(
    make_case_record: Any,
) -> None:
    submitted = datetime(2026, 10, 2, 10, tzinfo=UTC)  # a Friday
    record = make_case_record(submitted_at=submitted)
    row = list_item(
        record, CaseSummary(filled_field_ids=frozenset({"F01"})), active_field_set()
    )
    assert row.stage == "submitted"
    assert row.due == "2026-10-06"  # Monday the 5th is day one, Tuesday day two
    assert row.submitted == "2026-10-02"


def test_a_closed_row_has_no_due_date_and_counts_open_conditions(
    make_case_record: Any,
) -> None:
    from fcrm_contracts.enums import CaseState

    record = make_case_record(state=CaseState.CLOSED, submitted_at=NOW)
    row = list_item(record, CaseSummary(open_conditions=2), active_field_set())
    assert (row.due, row.next_action, row.needs_response) == (
        None,
        "2 open conditions",
        False,
    )
    assert row.completion_pct is None  # nothing stored: it predates this system


def test_the_review_shows_a_corrected_value_beside_its_original(
    make_case_record: Any,
) -> None:
    record = make_case_record()
    original = FieldHistoryEntry(
        value="Q1 2027",
        origin="extracted",
        source=FieldSource(doc_id="doc_1", page=1, line_start=9),
        at=NOW,
    )
    row = FieldValueRow(
        case_id=record.case_id,
        field_id="F03",
        label="Go-live date",
        value="February 2027",
        origin="owner_declared",
        source=FieldSource(label="owner declared"),
        history=[original],
        created_at=NOW,
    )
    view = review_view(
        record,
        active_field_set(),
        rows=[row],
        docs=[doc()],
    )
    shown = next(r for r in view.rows if r.field_id == "F03")
    assert shown.original is not None
    assert (shown.original.value, shown.original.source.label) == (  # type: ignore[union-attr]
        "Q1 2027",
        "business_case.pdf p.1 l.9",
    )
    untouched = next(r for r in view.rows if r.field_id == "F01")
    assert (untouched.origin, untouched.value, untouched.original) == (
        "open",
        None,
        None,
    )
    assert view.required_card.field_set_version == "v6"
    assert view.required_card.labels[2] == "F03 Go-live date"


def test_a_blank_stored_value_is_still_open(make_case_record: Any) -> None:
    record = make_case_record()
    row = FieldValueRow(
        case_id=record.case_id,
        field_id="F01",
        label="x",
        value="   ",
        origin="answered",
        created_at=NOW,
    )
    view = review_view(record, active_field_set(), rows=[row], docs=[])
    assert next(r for r in view.rows if r.field_id == "F01").origin == "open"
    assert view.completion.filled == 0


def entry(**overrides: Any) -> ActivityEntry:
    base: dict[str, Any] = {
        "case_id": "FCRM-2026-0151",
        "seq": 3,
        "at": NOW,
        "type": "case.created",
        "visibility": "po",
        "summary": "You created the case",
        "actor": UiActor(id="u-pnair", role="fcrm_po", component="S1"),
    }
    return ActivityEntry(**{**base, **overrides})


@pytest.mark.parametrize(
    ("overrides", "who"),
    [
        ({}, "You"),
        ({"actor": UiActor(id="u-other", role="fcrm_po", component="S1")}, "You"),
        (
            {
                "type": "document.extracted",
                "actor": UiActor(
                    id="svc:caseflow", role="fcrm_service", component="caseflow"
                ),
            },
            "AI",
        ),
        (
            {
                "type": "intake.proposals_ready",
                "actor": UiActor(
                    id="svc:caseflow", role="fcrm_service", component="caseflow"
                ),
            },
            "AI",
        ),
        (
            {
                "type": "case.assigned",
                "actor": UiActor(
                    id="svc:caseflow", role="fcrm_service", component="caseflow"
                ),
            },
            "System",
        ),
        (
            {
                "type": "clarification.sent",
                "actor": UiActor(id="u-riyer", role="fcrm_analyst", component="S1"),
            },
            "FCRM",
        ),
    ],
)
def test_the_feed_says_who_did_it_without_naming_a_component(
    overrides: dict[str, Any], who: str
) -> None:
    event = activity_event(entry(**overrides), "u-pnair")
    assert event.actor == who
    assert event.id == "ev_3"
    assert set(event.model_dump()) == {"id", "at", "actor", "text"}
