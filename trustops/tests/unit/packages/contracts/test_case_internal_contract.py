"""The S1 internal contract H1's intake core and S1's view store both code against."""

from __future__ import annotations

import pytest
from fcrm_contracts.api.case_internal import (
    AssignAnalystRequest,
    CategoryProposalItem,
    DocumentPatch,
    GapQuestionItem,
    IntakePush,
    ItemsUpsert,
    ReviewItemUpsert,
    StatePatch,
    UiEventIn,
    ValueProposal,
)
from fcrm_contracts.enums import CaseState, ChangeType, ItemKind, ItemStatus
from fcrm_contracts.proposals.provenance import DocSourceRef
from pydantic import ValidationError

SPAN = DocSourceRef(doc_id="doc_a", page=1, line_start=9, line_end=9)


def test_the_intake_item_kinds_exist_next_to_the_gate_a_kinds():
    assert {ItemKind.CATEGORY, ItemKind.VALUE, ItemKind.GAP} <= set(ItemKind)
    assert ItemKind("critic_finding") is ItemKind.CRITIC_FINDING


def test_an_intake_push_round_trips_through_json():
    push = IntakePush(
        run_id="run_1",
        candidates_found=15,
        required_fields=18,
        values=[
            ValueProposal(
                field_id="F03",
                label="Go-live",
                value="Q1 2027",
                source=SPAN,
                quote="go-live Q1 2027",
                confidence=0.9,
            )
        ],
        categories=[
            CategoryProposalItem(
                change_type=ChangeType.VENDOR_ONBOARDING,
                label="Vendor onboarding",
                source=SPAN,
                quote="payout partner",
                confidence=0.94,
                gate_id="gate_x",
            )
        ],
        gaps=[
            GapQuestionItem(question_id="q1", label="Limits", text="?", blocking=True)
        ],
    )
    assert IntakePush.model_validate_json(push.model_dump_json()) == push


def test_a_confidence_outside_zero_to_one_is_refused():
    with pytest.raises(ValidationError):
        ValueProposal(
            field_id="F03",
            label="x",
            value="y",
            source=SPAN,
            quote="q",
            confidence=1.5,
        )


@pytest.mark.parametrize(
    "model",
    [
        IntakePush,
        ItemsUpsert,
        StatePatch,
        UiEventIn,
        DocumentPatch,
        AssignAnalystRequest,
    ],
)
def test_internal_requests_forbid_unknown_fields(model):
    assert model.model_config["extra"] == "forbid"


def test_a_review_item_defaults_to_proposed_and_carries_a_stable_id():
    item = ReviewItemUpsert(item_id="itm_1", kind=ItemKind.CATEGORY)
    assert item.status is ItemStatus.PROPOSED
    assert ItemsUpsert(run_id="run_1", items=[item]).items[0].item_id == "itm_1"


def test_a_state_patch_changes_only_what_is_set():
    patch = StatePatch(stage=CaseState.SCREENING)
    assert patch.model_dump(exclude_none=True) == {"stage": CaseState.SCREENING}


def test_ui_visibility_is_restricted():
    with pytest.raises(ValidationError):
        UiEventIn.model_validate(
            {
                "type": "x",
                "visibility": "committee",
                "summary": "s",
                "actor": {"id": "a", "role": "r", "component": "c"},
            }
        )
