"""S1's view store (``InMemoryViewStore``, the unit-test twin of
``MongoViewStore``: same rules, no database): item upserts that never undo a
decision, the intake push that never overwrites a value a person set, the PO's
correction that keeps the original, documents, and events enqueued with writes.
The Mongo implementation is exercised on a real replica set in
``tests/integration/live_mongo/test_case_foundation_live.py``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Optional

import pytest
from fcrm_case.domain.intake import items_from_push
from fcrm_case.domain.views import (
    DecisionRow,
    DocumentRow,
    FieldSource,
    field_value_id,
)
from fcrm_case.views_store import InMemoryViewStore
from fcrm_common.errors import ConflictError, ValidationError
from fcrm_common.events import build_envelope
from fcrm_contracts.api.case_internal import (
    CategoryProposalItem,
    DocumentPatch,
    FactFlagItem,
    GapQuestionItem,
    IntakePush,
    PrecedentMatch,
    ReusableField,
    ReviewItemUpsert,
    ValueAlternative,
    ValueProposal,
)
from fcrm_contracts.enums import ChangeType, ItemKind, ItemStatus
from fcrm_contracts.proposals.provenance import ClauseSourceRef, DocSourceRef

CASE = "FCRM-2026-0151"
SPAN = DocSourceRef(doc_id="doc_a", page=1, line_start=2, line_end=3)
NOW = datetime(2026, 10, 5, 10, 0, tzinfo=UTC)


def value(field_id: str = "F03", text: str = "Q1 2027", **extra: Any) -> ValueProposal:
    return ValueProposal(
        field_id=field_id,
        label=f"label {field_id}",
        value=text,
        source=SPAN,
        quote="quote",
        confidence=0.9,
        **extra,
    )


def push(
    *,
    values: Optional[list[ValueProposal]] = None,
    run_id: str = "run_1",
) -> IntakePush:
    return IntakePush(
        run_id=run_id,
        candidates_found=15,
        required_fields=18,
        values=values if values is not None else [value("F03"), value("F05", "$48M")],
        categories=[
            CategoryProposalItem(
                change_type=ChangeType.VENDOR_ONBOARDING,
                label="Vendor onboarding",
                source=SPAN,
                quote="payout partner",
                confidence=0.94,
                gate_id="gate_cat_1",
            ),
            CategoryProposalItem(
                change_type=ChangeType.NEW_GEOGRAPHY,
                label="New geography",
                source=SPAN,
                quote="Mexico",
                confidence=0.9,
                gate_id="gate_cat_2",
            ),
        ],
        gaps=[
            GapQuestionItem(
                question_id="q1",
                field_id="F31",
                label="Limits",
                text="What are the limits?",
                blocking=True,
                gate_id="gate_gap_1",
            ),
            GapQuestionItem(
                question_id="q2", label="Volume", text="Monthly volume?", blocking=False
            ),
        ],
        precedent=PrecedentMatch(
            case_id="FCRM-2026-0118",
            title="Guatemala corridor",
            unit="Payments",
            closed_month="2026-06",
            similarity=0.81,
            fields=[
                ReusableField(
                    field_id="F31", label="Limits", value="$2,999", reusable=True
                )
            ],
        ),
        flags=[FactFlagItem(field_id="F21", text="cash pickup", kind="new_fact")],
    )


def upsert(item_id: str = "itm_a", **kwargs: Any) -> ReviewItemUpsert:
    fields: dict[str, Any] = {
        "item_id": item_id,
        "kind": ItemKind.CLAUSE,
        "subject": {"clause_id": "C-1"},
        "proposed": {"quote": "text"},
        "source_refs": [ClauseSourceRef(clause_id="C-1", corpus_v=14)],
    }
    return ReviewItemUpsert(**{**fields, **kwargs})


def decided_by(by: str = "u-riyer") -> DecisionRow:
    return DecisionRow(value={"ok": True}, reason="checked", by=by, at=NOW)


# ── review items ────────────────────────────────────────────────────────────


async def test_upsert_items_inserts_then_rewrites_content_in_place(
    view_store: InMemoryViewStore,
) -> None:
    assert await view_store.upsert_items(
        CASE, "run_1", [upsert("itm_a"), upsert("itm_b")]
    ) == [
        "itm_a",
        "itm_b",
    ]
    await view_store.upsert_items(
        CASE, "run_2", [upsert("itm_a", proposed={"quote": "new"})]
    )
    rows = {r.item_id: r for r in await view_store.list_items(CASE)}
    assert set(rows) == {"itm_a", "itm_b"}
    assert rows["itm_a"].proposed == {"quote": "new"}
    assert rows["itm_a"].run_id == "run_2"
    assert rows["itm_b"].run_id == "run_1"


async def test_an_upsert_never_reverts_a_decision_or_its_status(
    view_store: InMemoryViewStore,
) -> None:
    await view_store.upsert_items(CASE, "run_1", [upsert("itm_a")])
    await view_store.decide_item(
        CASE, "itm_a", status=ItemStatus.CONFIRMED, decided=decided_by()
    )
    await view_store.upsert_items(CASE, "run_1", [upsert("itm_a")])
    (row,) = await view_store.list_items(CASE)
    assert row.status == ItemStatus.CONFIRMED
    assert row.decided is not None
    assert row.decided.by == "u-riyer"


async def test_the_same_upsert_twice_is_the_same_state(
    view_store: InMemoryViewStore,
) -> None:
    items = [upsert("itm_a", gate_id="gate_1"), upsert("itm_b", kind=ItemKind.GAP)]
    await view_store.upsert_items(CASE, "run_1", items)
    first = await view_store.list_items(CASE)
    await view_store.upsert_items(CASE, "run_1", items)
    assert await view_store.list_items(CASE) == first


async def test_list_items_filters_by_kind(view_store: InMemoryViewStore) -> None:
    await view_store.upsert_items(
        CASE, "run_1", [upsert("itm_a"), upsert("itm_b", kind=ItemKind.CONTROL)]
    )
    only = await view_store.list_items(CASE, ItemKind.CONTROL)
    assert [r.item_id for r in only] == ["itm_b"]
    assert await view_store.list_items("FCRM-2026-0001") == []


async def test_a_proposal_id_may_belong_to_one_item_of_a_case(
    view_store: InMemoryViewStore,
) -> None:
    await view_store.upsert_items(CASE, "run_1", [upsert("itm_a", proposal_id="prp_1")])
    await view_store.upsert_items(CASE, "run_1", [upsert("itm_a", proposal_id="prp_1")])
    with pytest.raises(ConflictError):
        await view_store.upsert_items(
            CASE, "run_1", [upsert("itm_b", proposal_id="prp_1")]
        )
    # the same proposal id on another case is a different item
    await view_store.upsert_items(
        "FCRM-2026-0001", "run_1", [upsert("itm_c", proposal_id="prp_1")]
    )


async def test_an_item_id_cannot_be_taken_over_by_another_case(
    view_store: InMemoryViewStore,
) -> None:
    await view_store.upsert_items(CASE, "run_1", [upsert("itm_a")])
    with pytest.raises(ConflictError):
        await view_store.upsert_items("FCRM-2026-0001", "run_1", [upsert("itm_a")])


async def test_deciding_an_item_enqueues_its_events_with_the_write(
    view_store: InMemoryViewStore,
) -> None:
    await view_store.upsert_items(CASE, "run_1", [upsert("itm_a")])
    event = build_envelope("gate.decided", {"gate_id": "g"}, case_id=CASE)
    row = await view_store.decide_item(
        CASE,
        "itm_a",
        status=ItemStatus.OVERRIDDEN,
        decided=decided_by(),
        events=[event],
    )
    assert row is not None
    assert row.status == ItemStatus.OVERRIDDEN
    assert view_store.events == [event]
    assert (
        await view_store.decide_item(
            CASE,
            "nope",
            status=ItemStatus.CONFIRMED,
            decided=decided_by(),
            events=[event],
        )
        is None
    )
    assert view_store.events == [
        event
    ]  # nothing enqueued for a write that did not happen


# ── the intake push ─────────────────────────────────────────────────────────


async def test_a_push_materialises_values_items_and_the_intake_view(
    view_store: InMemoryViewStore,
) -> None:
    applied = await view_store.apply_intake_push(CASE, push())
    assert (applied.values_written, applied.values_kept) == (2, 0)
    assert (applied.categories, applied.gaps) == (2, 2)

    values = {r.field_id: r for r in await view_store.list_field_values(CASE)}
    assert values["F03"].value == "Q1 2027"
    assert values["F03"].origin == "extracted"
    assert values["F03"].source is not None
    assert values["F03"].source.as_doc_ref() == SPAN
    assert values["F03"].confidence == 0.9

    categories = await view_store.list_items(CASE, ItemKind.CATEGORY)
    assert sorted(i.gate_id or "" for i in categories) == ["gate_cat_1", "gate_cat_2"]
    assert {i.subject["change_type"] for i in categories} == {
        "vendor_onboarding",
        "new_geography",
    }
    assert all(i.status == ItemStatus.PROPOSED for i in categories)
    gaps = {
        i.subject["question_id"]: i
        for i in await view_store.list_items(CASE, ItemKind.GAP)
    }
    assert gaps["q1"].gate_id == "gate_gap_1"
    assert gaps["q2"].gate_id is None

    view = await view_store.get_intake_view(CASE)
    assert view is not None
    assert view.candidates_found == 15
    assert view.required_fields == 18
    assert view.precedent is not None
    assert view.precedent["case_id"] == "FCRM-2026-0118"
    assert view.flags == [
        {"field_id": "F21", "text": "cash pickup", "kind": "new_fact"}
    ]
    assert await view_store.get_intake_view("FCRM-2026-0001") is None


async def test_the_same_push_twice_is_exactly_the_same_state(
    view_store: InMemoryViewStore,
) -> None:
    await view_store.apply_intake_push(CASE, push())
    before = (
        await view_store.list_field_values(CASE, full_history=True),
        await view_store.list_items(CASE),
        await view_store.get_intake_view(CASE),
    )
    await view_store.apply_intake_push(CASE, push())
    after = (
        await view_store.list_field_values(CASE, full_history=True),
        await view_store.list_items(CASE),
        await view_store.get_intake_view(CASE),
    )
    assert after == before


@pytest.mark.parametrize("origin", ["owner_declared", "reused", "answered"])
async def test_a_push_never_overwrites_a_value_a_person_set(
    view_store: InMemoryViewStore, origin: Any
) -> None:
    await view_store.write_field_value(
        CASE,
        "F03",
        label="Go-live",
        value="Q3 2027",
        origin=origin,
        source=FieldSource(label="the PO said so"),
        by="u-pnair",
    )
    applied = await view_store.apply_intake_push(
        CASE, push(values=[value("F03", "Q1 2027"), value("F05", "$48M")])
    )
    assert (applied.values_written, applied.values_kept) == (1, 1)
    values = {r.field_id: r for r in await view_store.list_field_values(CASE)}
    assert values["F03"].value == "Q3 2027"
    assert values["F03"].origin == origin
    assert values["F05"].value == "$48M"


async def test_a_second_extraction_replaces_an_earlier_extracted_value(
    view_store: InMemoryViewStore,
) -> None:
    await view_store.apply_intake_push(CASE, push(values=[value("F03", "Q1 2027")]))
    await view_store.apply_intake_push(
        CASE, push(values=[value("F03", "Q2 2027")], run_id="run_2")
    )
    (row,) = await view_store.list_field_values(CASE)
    assert row.value == "Q2 2027"
    assert row.origin == "extracted"


async def test_a_replayed_push_does_not_revert_a_decided_category(
    view_store: InMemoryViewStore,
) -> None:
    await view_store.apply_intake_push(CASE, push())
    (first, *_rest) = await view_store.list_items(CASE, ItemKind.CATEGORY)
    await view_store.decide_item(
        CASE, first.item_id, status=ItemStatus.CONFIRMED, decided=decided_by("u-pnair")
    )
    await view_store.apply_intake_push(CASE, push())
    again = {i.item_id: i for i in await view_store.list_items(CASE, ItemKind.CATEGORY)}
    assert again[first.item_id].status == ItemStatus.CONFIRMED


def test_item_ids_are_stable_and_distinct_per_subject() -> None:
    first = items_from_push(CASE, push())
    second = items_from_push(CASE, push())
    assert [i.item_id for i in first] == [i.item_id for i in second]
    assert len({i.item_id for i in first}) == 4
    assert items_from_push("FCRM-2026-0001", push())[0].item_id != first[0].item_id


def test_a_question_id_with_the_id_separator_is_a_validation_error() -> None:
    bad = push()
    bad.gaps[0] = bad.gaps[0].model_copy(update={"question_id": "a|b"})
    with pytest.raises(ValidationError):
        items_from_push(CASE, bad)


# ── field values ────────────────────────────────────────────────────────────


async def test_a_correction_keeps_the_extracted_original_in_history(
    view_store: InMemoryViewStore,
) -> None:
    await view_store.apply_intake_push(CASE, push(values=[value("F05", "$48M")]))
    corrected = await view_store.write_field_value(
        CASE,
        "F05",
        label="Volume",
        value="$52M",
        origin="owner_declared",
        source=None,
        by="u-pnair",
    )
    assert corrected.value == "$52M"
    assert corrected.origin == "owner_declared"
    assert [(h.value, h.origin) for h in corrected.history] == [("$48M", "extracted")]
    assert corrected.updated_by == "u-pnair"
    assert corrected.history[0].source is not None
    assert corrected.history[0].source.page == 1

    again = await view_store.write_field_value(
        CASE,
        "F05",
        label="Volume",
        value="$52M",
        origin="owner_declared",
        source=None,
        by="u-pnair",
    )
    assert again == corrected  # an identical write adds no history

    third = await view_store.write_field_value(
        CASE,
        "F05",
        label="Volume",
        value="$55M",
        origin="owner_declared",
        source=None,
        by="u-pnair",
    )
    assert [h.value for h in third.history] == ["$48M", "$52M"]


async def test_the_default_read_returns_only_the_original_history_entry(
    view_store: InMemoryViewStore,
) -> None:
    await view_store.apply_intake_push(CASE, push(values=[value("F05", "$48M")]))
    for text in ("$52M", "$55M"):
        await view_store.write_field_value(
            CASE,
            "F05",
            label="Volume",
            value=text,
            origin="owner_declared",
            source=None,
            by="u",
        )
    (lean,) = await view_store.list_field_values(CASE)
    (full,) = await view_store.list_field_values(CASE, full_history=True)
    assert [h.value for h in lean.history] == ["$48M"]
    assert [h.value for h in full.history] == ["$48M", "$52M"]
    assert field_value_id(CASE, "F05") == f"{CASE}:F05"


async def test_a_field_write_enqueues_its_events_in_the_same_step(
    view_store: InMemoryViewStore,
) -> None:
    event = build_envelope("gate.decided", {"gate_id": "g"}, case_id=CASE)
    await view_store.write_field_value(
        CASE,
        "F05",
        label="Volume",
        value="$52M",
        origin="owner_declared",
        source=None,
        by="u-pnair",
        events=[event],
    )
    assert view_store.events == [event]


# ── documents ───────────────────────────────────────────────────────────────


def document(doc_id: str = "doc_a", **kwargs: Any) -> DocumentRow:
    fields: dict[str, Any] = {
        "doc_id": doc_id,
        "case_id": CASE,
        "name": "business_case.pdf",
        "status": "stored",
        "bucket": "uploads",
        "key": f"{CASE}/{doc_id}/business_case.pdf",
        "size_bytes": 1024,
        "content_type": "application/pdf",
        "uploaded_at": NOW,
    }
    return DocumentRow(**{**fields, **kwargs})


async def test_documents_are_inserted_with_their_events_and_listed_oldest_first(
    view_store: InMemoryViewStore,
) -> None:
    event = build_envelope("case.document_stored", {"doc_id": "doc_a"}, case_id=CASE)
    await view_store.insert_documents(
        [document("doc_b", uploaded_at=NOW.replace(hour=11)), document("doc_a")],
        events=[event],
    )
    assert [d.doc_id for d in await view_store.list_documents(CASE)] == [
        "doc_a",
        "doc_b",
    ]
    assert view_store.events == [event]
    assert await view_store.list_documents("FCRM-2026-0001") == []
    with pytest.raises(ConflictError):
        await view_store.insert_documents([document("doc_a")])


async def test_a_document_patch_records_what_h3_made_and_is_idempotent(
    view_store: InMemoryViewStore,
) -> None:
    await view_store.insert_documents([document()])
    patch = DocumentPatch(
        status="extracted",
        canonical_key="canonical/FCRM-2026-0151/doc_a.md",
        anchored_key="canonical/FCRM-2026-0151/doc_a.anchored.md",
        pages=14,
        page_map={"1": (1, 40)},
        guard1={"clean": True},
    )
    first = await view_store.patch_document("doc_a", patch)
    second = await view_store.patch_document("doc_a", patch)
    assert first is not None
    assert second == first
    assert first.status == "extracted"
    assert first.pages == 14
    assert first.guard1 == {"clean": True}
    # a later patch that leaves a field unset does not blank it
    third = await view_store.patch_document("doc_a", DocumentPatch(status="extracted"))
    assert third is not None
    assert third.pages == 14
    assert await view_store.patch_document("missing", patch) is None
    assert await view_store.get_document("missing") is None


async def test_a_push_keeps_the_other_values_found_and_a_new_extraction_replaces_them(
    view_store: InMemoryViewStore,
) -> None:
    """D-132: what the owner may switch to is stored with the value, and moves
    with it when a later extraction replaces the value."""
    first = value(
        "F04",
        "Registered charities, income below GBP 5 million",
        candidates=3,
        alternatives=[
            ValueAlternative(value="Registered charities", source=SPAN),
            ValueAlternative(value="Charities and CICs", source=SPAN),
        ],
    )
    await view_store.apply_intake_push(CASE, push(values=[first]))
    (row,) = await view_store.list_field_values(CASE)
    assert [a.value for a in row.alternatives] == [
        "Registered charities",
        "Charities and CICs",
    ]
    assert row.alternatives[0].source.doc_id == SPAN.doc_id

    await view_store.apply_intake_push(
        CASE, push(values=[value("F04", "Charities")], run_id="run_2")
    )
    (row,) = await view_store.list_field_values(CASE)
    assert (row.value, row.alternatives) == ("Charities", [])
