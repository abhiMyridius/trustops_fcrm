"""How an :class:`~fcrm_contracts.api.case_internal.IntakePush` becomes S1's
rows. Pure, no I/O: both view stores call these so the Mongo and in-memory
twins materialise a push identically.

Every id derived here comes from :func:`fcrm_common.ids.deterministic_id` over
the case and what the item is about, so the same push, replayed after a harness
step failed, addresses the same rows (idempotent by construction).
"""

from __future__ import annotations

from fcrm_common.errors import ValidationError
from fcrm_common.ids import deterministic_id
from fcrm_contracts.api.case_internal import (
    CategoryProposalItem,
    GapQuestionItem,
    IntakePush,
    ReviewItemUpsert,
    ValueProposal,
)
from fcrm_contracts.enums import ItemKind, ItemStatus

from fcrm_case.domain.views import FieldSource, IntakeViewRow


def item_id_for(case_id: str, kind: ItemKind, *parts: str) -> str:
    """The stable ``itm_`` id of the item of ``kind`` about ``parts`` on
    ``case_id``.

    Raises:
        fcrm_common.errors.ValidationError: A part contains ``|`` (the id
            separator), so no stable id exists for it.
    """
    try:
        return deterministic_id("itm", case_id, kind.value, *parts)
    except ValueError as exc:
        raise ValidationError(f"cannot derive an item id: {exc}") from exc


def category_item(case_id: str, item: CategoryProposalItem) -> ReviewItemUpsert:
    """The ``category`` review item the PO confirms or removes."""
    return ReviewItemUpsert(
        item_id=item_id_for(case_id, ItemKind.CATEGORY, item.change_type.value),
        kind=ItemKind.CATEGORY,
        gate_id=item.gate_id,
        subject={"change_type": item.change_type.value, "label": item.label},
        proposed={
            "source": item.source.model_dump(mode="json"),
            "quote": item.quote,
            "confidence": item.confidence,
        },
        source_refs=[item.source],
        status=ItemStatus.PROPOSED,
    )


def gap_item(case_id: str, gap: GapQuestionItem) -> ReviewItemUpsert:
    """The ``gap`` review item the PO answers."""
    return ReviewItemUpsert(
        item_id=item_id_for(case_id, ItemKind.GAP, gap.question_id),
        kind=ItemKind.GAP,
        gate_id=gap.gate_id,
        subject={
            "question_id": gap.question_id,
            "field_id": gap.field_id,
            "label": gap.label,
            "text": gap.text,
            "blocking": gap.blocking,
        },
        status=ItemStatus.PROPOSED,
    )


def items_from_push(case_id: str, push: IntakePush) -> list[ReviewItemUpsert]:
    """Every gated item a push carries: its categories, then its gaps."""
    return [
        *(category_item(case_id, c) for c in push.categories),
        *(gap_item(case_id, g) for g in push.gaps),
    ]


def intake_view_from_push(case_id: str, push: IntakePush) -> IntakeViewRow:
    """What a push leaves in ``intake_views``: the precedent, the number of
    candidates found and the fact flags. Carries no timestamp, so a replay
    rewrites an identical row.
    """
    return IntakeViewRow(
        case_id=case_id,
        run_id=push.run_id,
        candidates_found=push.candidates_found,
        required_fields=push.required_fields,
        precedent=(
            push.precedent.model_dump(mode="json")
            if push.precedent is not None
            else None
        ),
        flags=[flag.model_dump(mode="json") for flag in push.flags],
    )


def value_set_fields(value: ValueProposal) -> dict[str, object]:
    """The fields an extracted value sets on its ``field_values`` row (the
    ones a re-extraction may overwrite).
    """
    return {
        "field_id": value.field_id,
        "label": value.label,
        "value": value.value,
        "origin": "extracted",
        "source": FieldSource.from_doc_ref(value.source).model_dump(mode="python"),
        "confidence": value.confidence,
        "candidates": value.candidates,
        "alternatives": [
            {
                "value": alternative.value,
                "source": FieldSource.from_doc_ref(alternative.source).model_dump(
                    mode="python"
                ),
            }
            for alternative in value.alternatives
        ],
    }
