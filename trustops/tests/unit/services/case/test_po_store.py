"""``fcrm_case.po_store.InMemoryPoStore``: the contract every PO store keeps, in
the form that needs no database. ``tests/integration/live_mongo/
test_po_surface_live.py`` repeats the atomic parts on a real replica set (the
transaction itself), so the two implementations are held to the same rules.

The rules: a command applies whole or not at all; the first decision on a gate
emits its event and a later one does not; an activity row tied to a gate exists
only when that gate emitted; a refusal leaves no trace.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_case.domain.po_commands import (
    ActivityDraft,
    FieldWrite,
    GateEmission,
    ItemDecision,
    PoCommand,
    TypeDelta,
)
from fcrm_case.domain.po_rows import (
    CapturedBy,
    IntegrationRow,
    SnapshotCompletion,
    SubmissionVersionRow,
)
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import (
    DecisionRow,
    DocumentRow,
    FieldSource,
    ReviewItemRow,
)
from fcrm_case.po_errors import (
    AlreadySubmittedError,
    CaseClosedError,
)
from fcrm_case.po_store import InMemoryPoStore, gate_key
from fcrm_common.errors import ConflictError, NotFoundError
from fcrm_common.events import build_envelope
from fcrm_contracts.api.case_internal import UiActor, UiEventIn
from fcrm_contracts.enums import CaseState, ChangeType, ItemKind, ItemStatus

if TYPE_CHECKING:
    from conftest import CaseService

CASE = "FCRM-2026-0151"
NOW = datetime(2026, 10, 1, 9, tzinfo=UTC)
ACTOR = UiActor(id="u-pnair", role="fcrm_po", component="S1")


@pytest.fixture
def po(case_service: CaseService, stored_case: CaseRecord) -> InMemoryPoStore:
    """The in-memory PO store over the demo case stored at intake."""
    return case_service.po


@pytest.fixture
def service(case_service: CaseService, stored_case: CaseRecord) -> CaseService:
    return case_service


def draft(summary: str, gate: str | None = None) -> ActivityDraft:
    return ActivityDraft(
        UiEventIn(
            type="gate.decided",
            visibility="po",
            summary=summary,
            actor=ACTOR,
        ),
        only_with_gate=gate,
    )


def gate(gate_type: str, key: str) -> GateEmission:
    return GateEmission(
        gate_type,
        key,
        build_envelope("gate.decided", {"key": key}, case_id=CASE, run_id="run_1"),
    )


def category_item(change_type: str) -> ReviewItemRow:
    return ReviewItemRow(
        item_id=f"itm_{change_type}",
        case_id=CASE,
        kind=ItemKind.CATEGORY,
        status=ItemStatus.PROPOSED,
        subject={"change_type": change_type, "label": change_type},
        created_at=NOW,
    )


def document(doc_id: str = "doc_1") -> DocumentRow:
    return DocumentRow(
        doc_id=doc_id,
        case_id=CASE,
        name=f"{doc_id}.pdf",
        status="stored",
        bucket="uploads",
        key=f"{CASE}/{doc_id}/{doc_id}.pdf",
        size_bytes=10,
        content_type="application/pdf",
        uploaded_at=NOW,
    )


def submission(version: int = 1) -> SubmissionVersionRow:
    return SubmissionVersionRow(
        version_id=f"sv_{version}",
        case_id=CASE,
        version=version,
        captured_at=NOW,
        captured_by=CapturedBy(sub="u-pnair", role="fcrm_po"),
        reason="submit",
        completion=SnapshotCompletion(filled=1, required=6),
    )


def write(field_id: str = "F03", value: str = "Q1 2027") -> FieldWrite:
    return FieldWrite(field_id, "Go-live date", value, "owner_declared", None)


async def test_a_command_applies_every_part_together(
    service: CaseService, po: InMemoryPoStore
) -> None:
    service.views.items["itm_new_product"] = category_item("new_product")
    decided = DecisionRow(value={"decision": "confirm"}, by="u-pnair", at=NOW)
    done = await po.commit(
        PoCommand(
            case_id=CASE,
            actor="u-pnair",
            type_delta=TypeDelta(ChangeType.NEW_PRODUCT, True),
            fields=[write()],
            items=[ItemDecision("itm_new_product", ItemStatus.CONFIRMED, decided)],
            gates=[gate("po_confirm_category", "new_product")],
            documents=[document()],
            events=[build_envelope("case.document_stored", {}, case_id=CASE)],
            activity=[draft("You confirmed change type: new product")],
        )
    )
    assert done.emitted == {gate_key("po_confirm_category", "new_product")}
    assert [t.value for t in done.record.change_types] == ["new_product"]
    assert len(done.record.required_fields) == 6 + 5
    assert done.record.domains == ["product"]
    assert service.views.field_values[f"{CASE}:F03"].value == "Q1 2027"
    assert service.views.items["itm_new_product"].status == ItemStatus.CONFIRMED
    assert set(service.views.documents) == {"doc_1"}
    assert [e.type for e in service.store.events] == [
        "gate.decided",
        "case.document_stored",
    ]
    assert [(e.seq, e.summary) for e in done.entries] == [
        (1, "You confirmed change type: new product")
    ]
    assert service.store.records[CASE] == done.record
    assert done.record.activity_seq == 1


async def test_a_refusal_leaves_no_trace(
    service: CaseService, po: InMemoryPoStore
) -> None:
    """A review item that is not there stops the whole command: no value, no
    document, no event, no activity row, no ledger entry.
    """
    before = service.store.records[CASE]
    with pytest.raises(ConflictError, match="no longer there"):
        await po.commit(
            PoCommand(
                case_id=CASE,
                actor="u-pnair",
                fields=[write()],
                items=[
                    ItemDecision(
                        "itm_missing",
                        ItemStatus.CONFIRMED,
                        DecisionRow(by="u-pnair", at=NOW),
                    )
                ],
                gates=[gate("po_confirm_category", "new_product")],
                documents=[document()],
                events=[build_envelope("case.document_stored", {}, case_id=CASE)],
                activity=[draft("x")],
            )
        )
    assert service.views.field_values == {}
    assert service.views.documents == {}
    assert service.store.events == []
    assert service.store.activity.entries == []
    assert po.decisions == {}
    assert service.store.records[CASE] == before


async def test_a_duplicate_document_stops_the_whole_command(
    service: CaseService, po: InMemoryPoStore
) -> None:
    service.views.documents["doc_1"] = document()
    with pytest.raises(ConflictError, match="already exists"):
        await po.commit(
            PoCommand(
                case_id=CASE,
                actor="u-pnair",
                documents=[document()],
                events=[build_envelope("case.document_stored", {}, case_id=CASE)],
                activity=[draft("You uploaded doc_1.pdf")],
            )
        )
    assert service.store.events == []
    assert service.store.activity.entries == []


async def test_the_first_decision_on_a_gate_emits_and_a_later_one_does_not(
    service: CaseService, po: InMemoryPoStore
) -> None:
    command = PoCommand(
        case_id=CASE,
        actor="u-pnair",
        gates=[gate("po_confirm_value", "F03")],
        activity=[
            draft("You confirmed F03", gate_key("po_confirm_value", "F03")),
            draft("always"),
        ],
    )
    first = await po.commit(command)
    second = await po.commit(command)
    assert first.emitted == {gate_key("po_confirm_value", "F03")}
    assert second.emitted == frozenset()
    assert len(service.store.events) == 1
    # the row tied to the gate is staged only when the gate emitted
    assert [e.summary for e in service.store.activity.entries] == [
        "You confirmed F03",
        "always",
        "always",
    ]
    assert [e.seq for e in service.store.activity.entries] == [1, 2, 3]
    assert service.store.records[CASE].activity_seq == 3
    (decision,) = po.decisions.values()
    assert (decision.gate_type, decision.key, decision.decided_by) == (
        "po_confirm_value",
        "F03",
        "u-pnair",
    )


async def test_the_ledger_is_per_gate_not_per_type(
    service: CaseService, po: InMemoryPoStore
) -> None:
    await po.commit(
        PoCommand(
            case_id=CASE,
            actor="u-pnair",
            gates=[gate("po_confirm_value", "F03"), gate("po_confirm_value", "F04")],
        )
    )
    again = await po.commit(
        PoCommand(
            case_id=CASE,
            actor="u-pnair",
            gates=[gate("po_confirm_value", "F03"), gate("po_confirm_value", "F05")],
        )
    )
    assert again.emitted == {gate_key("po_confirm_value", "F05")}
    assert len(service.store.events) == 3


async def test_confirming_and_then_removing_a_type_changes_the_required_set(
    po: InMemoryPoStore,
) -> None:
    confirmed = await po.commit(
        PoCommand(
            case_id=CASE,
            actor="u-pnair",
            type_delta=TypeDelta(ChangeType.NEW_GEOGRAPHY, True),
        )
    )
    assert len(confirmed.record.required_fields) == 6 + 4
    again = await po.commit(
        PoCommand(
            case_id=CASE,
            actor="u-pnair",
            type_delta=TypeDelta(ChangeType.NEW_GEOGRAPHY, True),
        )
    )
    assert again.record.change_types == confirmed.record.change_types  # not doubled
    removed = await po.commit(
        PoCommand(
            case_id=CASE,
            actor="u-pnair",
            type_delta=TypeDelta(ChangeType.NEW_GEOGRAPHY, False),
        )
    )
    assert removed.record.change_types == []
    assert len(removed.record.required_fields) == 6
    assert removed.record.domains == []


async def test_a_type_resolves_against_the_field_set_the_case_pinned(
    service: CaseService, po: InMemoryPoStore, make_case_record: Any
) -> None:
    service.store.records[CASE] = make_case_record(fieldset_v=7)
    from fcrm_common.errors import UpstreamUnavailableError

    with pytest.raises(UpstreamUnavailableError, match="v7 is not available"):
        await po.commit(
            PoCommand(
                case_id=CASE,
                actor="u-pnair",
                type_delta=TypeDelta(ChangeType.NEW_GEOGRAPHY, True),
            )
        )


async def test_a_value_written_twice_keeps_the_first_as_history(
    service: CaseService, po: InMemoryPoStore
) -> None:
    extracted = FieldWrite(
        "F03", "Go-live date", "Q1 2027", "extracted", FieldSource(label="a")
    )
    await po.commit(PoCommand(case_id=CASE, actor="u-pnair", fields=[extracted]))
    await po.commit(
        PoCommand(case_id=CASE, actor="u-pnair", fields=[write(value="February 2027")])
    )
    row = service.views.field_values[f"{CASE}:F03"]
    assert (row.value, row.origin) == ("February 2027", "owner_declared")
    assert [h.value for h in row.history] == ["Q1 2027"]
    # writing the same value again is not a change
    await po.commit(
        PoCommand(case_id=CASE, actor="u-pnair", fields=[write(value="February 2027")])
    )
    assert len(service.views.field_values[f"{CASE}:F03"].history) == 1


async def test_submitting_stamps_the_case_and_a_second_version_one_is_refused(
    service: CaseService, po: InMemoryPoStore
) -> None:
    done = await po.commit(
        PoCommand(
            case_id=CASE,
            actor="u-pnair",
            submission=submission(),
            events=[build_envelope("case.submitted", {}, case_id=CASE)],
        )
    )
    assert done.record.submitted_at == NOW
    assert done.record.current_submission_v == 1
    assert (await po.get_submission(CASE, 1)) is not None
    assert (await po.get_submission(CASE, 2)) is None
    # the case is submitted now: any further PO write is refused up front
    with pytest.raises(AlreadySubmittedError):
        await po.commit(PoCommand(case_id=CASE, actor="u-pnair", fields=[write()]))
    assert len(service.store.events) == 1


async def test_the_submission_is_unique_per_case_and_version(
    service: CaseService, po: InMemoryPoStore
) -> None:
    po.submissions["sv_1"] = submission()
    with pytest.raises(AlreadySubmittedError):
        await po.commit(
            PoCommand(
                case_id=CASE,
                actor="u-pnair",
                submission=submission(),
                events=[build_envelope("case.submitted", {}, case_id=CASE)],
            )
        )
    assert service.store.events == []
    assert service.store.records[CASE].submitted_at is None


@pytest.mark.parametrize("state", [CaseState.CLOSED, CaseState.DECIDE])
async def test_a_decided_or_closed_case_is_closed_to_every_command(
    service: CaseService,
    po: InMemoryPoStore,
    set_case_state: Any,
    state: CaseState,
) -> None:
    set_case_state(CASE, state)
    with pytest.raises(CaseClosedError):
        await po.commit(PoCommand(case_id=CASE, actor="u-pnair", fields=[write()]))
    assert service.views.field_values == {}


async def test_a_discarded_draft_reads_as_missing_afterwards(
    service: CaseService, po: InMemoryPoStore
) -> None:
    done = await po.commit(
        PoCommand(case_id=CASE, actor="u-pnair", discard_reason="a duplicate of 0150")
    )
    assert done.record.discarded_at is not None
    assert done.record.discard_reason == "a duplicate of 0150"
    with pytest.raises(NotFoundError):
        await po.commit(PoCommand(case_id=CASE, actor="u-pnair", fields=[write()]))
    assert await po.owner_cases("u-pnair") == []
    page = await po.list_cases(
        "u-pnair", stage=None, needs_response=False, limit=10, cursor=None
    )
    assert page.items == []
    assert page.total == 0


async def test_an_unknown_case_is_not_found(po: InMemoryPoStore) -> None:
    with pytest.raises(NotFoundError):
        await po.commit(PoCommand(case_id="FCRM-2026-9999", actor="u-pnair"))


async def test_a_new_case_is_stored_with_its_first_activity_row_and_event(
    service: CaseService, po: InMemoryPoStore, make_case_record: Any
) -> None:
    record = make_case_record("FCRM-2026-0200")
    event = build_envelope("case.created", {}, case_id="FCRM-2026-0200")
    created = await po.create_case(record, event, draft("You created the case").event)
    assert created.record.activity_seq == 1
    assert service.store.records["FCRM-2026-0200"] == created.record
    assert [e.seq for e in created.entries] == [1]
    assert service.store.events == [event]


async def test_summaries_count_what_a_page_needs_in_one_pass(
    service: CaseService, po: InMemoryPoStore
) -> None:
    service.views.documents["d1"] = document("d1")
    service.views.documents["d2"] = document("d2").model_copy(
        update={"status": "failed"}
    )
    service.views.items["itm_new_product"] = category_item("new_product")
    po.open_threads[CASE] = 2
    await po.commit(PoCommand(case_id=CASE, actor="u-pnair", fields=[write()]))
    summary = (await po.summaries([CASE, "FCRM-2026-9999"]))[CASE]
    assert (summary.documents, summary.documents_unread) == (2, 1)
    assert summary.documents_unreadable == 1
    assert summary.pending_types == ("new_product",)
    assert summary.filled_field_ids == frozenset({"F03"})
    assert summary.open_threads == 2
    assert summary.intake_ready is False
    empty = (await po.summaries(["FCRM-2026-9999"]))["FCRM-2026-9999"]
    assert empty.documents == 0
    assert empty.filled_field_ids == frozenset()
    assert await po.summaries([]) == {}


async def test_integrations_are_stored_per_owner(po: InMemoryPoStore) -> None:
    row = IntegrationRow(
        user_id="u-pnair",
        integration_id="teams",
        status="connected",
        scope="#fcrm",
        updated_at=NOW,
    )
    await po.save_integration(row)
    assert await po.integration_rows("u-pnair") == {"teams": row}
    assert await po.integration_rows("u-other") == {}


async def test_the_unit_of_the_newest_case_and_the_average_days(
    service: CaseService, po: InMemoryPoStore, make_case_record: Any
) -> None:
    assert await po.latest_unit("u-pnair") == "Payments"
    assert await po.latest_unit("u-nobody") is None
    assert await po.average_days_to_decision() == 0.0
    service.store.records["FCRM-2026-0001"] = make_case_record(
        "FCRM-2026-0001",
        state=CaseState.CLOSED,
        created_at=datetime(2026, 1, 1, 9, tzinfo=UTC),
        decided_at=datetime(2026, 1, 5, 17, tzinfo=UTC),
    )
    service.store.records["FCRM-2026-0002"] = make_case_record(
        "FCRM-2026-0002",
        state=CaseState.CLOSED,
        created_at=datetime(2026, 1, 1, 9, tzinfo=UTC),
        decided_at=datetime(2026, 1, 2, 17, tzinfo=UTC),
    )
    assert await po.average_days_to_decision() == 2.5
