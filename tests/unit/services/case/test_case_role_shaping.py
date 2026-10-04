"""``fcrm_case.shaping``: mapping one stored ``CaseRecord`` onto the three
role-shaped wire contracts, and the structural proof that the PO shape
cannot carry a score/band/rating field (CLAUDE.md rule 3).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fcrm_case.domain.fieldset import FieldDef
from fcrm_case.domain.record import (
    CaseRecord,
    FieldValueRecord,
    SubmissionVersionRecord,
)
from fcrm_case.shaping import (
    completion_state,
    to_case_detail_analyst,
    to_case_detail_internal,
    to_case_detail_po,
    to_case_list_item_analyst,
    to_case_list_item_po,
)
from fcrm_contracts.api.case_analyst import CaseDetailAnalyst
from fcrm_contracts.api.case_internal import CaseDetailInternal
from fcrm_contracts.api.case_po import CaseDetailPO
from fcrm_contracts.enums import CaseState, ChangeType
from fcrm_contracts.versions import PinnedVersions
from pydantic import ValidationError

NOW = datetime(2026, 1, 12, tzinfo=UTC)
PINNED = PinnedVersions(
    scoring_v=3,
    corpus_v=14,
    controls_v=9,
    fieldset_v=6,
    jurisdictions_v="v2026-06",
    routing_v=4,
    prompts_v="2026-09-28",
)
REQUIRED_FIELDS = [
    FieldDef(field_id="F01", label="Product owner", always_required=True),
    FieldDef(field_id="F02", label="Description", always_required=True),
    FieldDef(field_id="F10", label="Vendor name", always_required=False),
]


def _record(**overrides: object) -> CaseRecord:
    base = dict(
        case_id="FCRM-2026-0200",
        title="Consumer remittances pilot",
        unit="Payments",
        owner="u-priya",
        created_by="u-priya",
        analyst_id="u-rahul",
        state=CaseState.REVIEW_AND_SUBMIT,
        change_types=[ChangeType.VENDOR_ONBOARDING],
        fieldset_v=6,
        completion_threshold=0.8,
        pinned_versions=PINNED,
        required_fields=REQUIRED_FIELDS,
        fields={
            "F01": FieldValueRecord(
                field_id="F01", label="Product owner", value="Priya Nair", filled_at=NOW
            )
        },
        created_at=NOW,
    )
    base.update(overrides)
    return CaseRecord(**base)  # type: ignore[arg-type]


class TestCaseDetailPOCannotCarryRestrictedFields:
    """Rule 3: the PO never sees a score, band, rating or harness field --
    enforced structurally (``extra="forbid"``), not just by this module's
    own care in building the PO shape.
    """

    def test_the_po_shape_has_no_score_band_or_rating_field(self) -> None:
        field_names = set(CaseDetailPO.model_fields)
        restricted = {"score", "band", "rating", "route", "composite", "harness"}
        assert field_names.isdisjoint(restricted)

    def test_constructing_the_po_shape_with_a_score_field_is_rejected(self) -> None:
        """Not "it doesn't today" -- a route bug that tried to smuggle a
        score through would fail Pydantic validation, not silently pass.
        """
        po = to_case_detail_po(_record())
        payload = po.model_dump()
        payload["score"] = {"composite": 10.4, "band": "3"}
        with pytest.raises(ValidationError, match="score"):
            CaseDetailPO.model_validate(payload)

    def test_the_analyst_shape_is_the_one_that_may_carry_a_score(self) -> None:
        assert "score" in CaseDetailAnalyst.model_fields
        assert "score" not in CaseDetailPO.model_fields


def test_to_case_detail_po_reports_completion_over_required_fields() -> None:
    po = to_case_detail_po(_record())
    assert po.required_field_count == 3
    assert po.required_fields_filled == 1
    assert po.completion_pct == pytest.approx(1 / 3)
    assert po.threshold_pct == 0.8
    assert [f.field_id for f in po.fields] == ["F01", "F02", "F10"]
    assert po.fields[0].value == "Priya Nair"
    assert po.fields[1].value is None


def test_to_case_detail_analyst_carries_the_assignment_and_no_score_yet() -> None:
    analyst = to_case_detail_analyst(_record())
    assert analyst.analyst_id == "u-rahul"
    assert analyst.score is None  # S1 does not own scoring; see shaping.py
    assert analyst.completion.required_count == 3


def test_to_case_detail_internal_adds_pinned_versions() -> None:
    internal = to_case_detail_internal(_record())
    assert isinstance(internal, CaseDetailInternal)
    assert internal.fieldset_v == 6
    assert internal.pinned_versions.scoring_v == 3
    # Still carries everything the analyst shape does.
    assert internal.analyst_id == "u-rahul"


def test_list_items_differ_by_role_but_agree_on_the_case() -> None:
    record = _record()
    po_item = to_case_list_item_po(record)
    analyst_item = to_case_list_item_analyst(record)
    assert po_item.case_id == analyst_item.case_id == record.case_id
    assert not hasattr(po_item, "analyst_id")
    assert analyst_item.analyst_id == "u-rahul"


def test_completion_state_reflects_the_pinned_threshold() -> None:
    state = completion_state(_record(completion_threshold=0.5))
    assert state.threshold_pct == 0.5
    assert state.all_always_required_filled is False  # F02 is always-required, unfilled


def test_submission_versions_are_shaped_with_their_own_diff() -> None:
    record = _record(
        submission_versions=[
            SubmissionVersionRecord(
                version=1,
                created_at=NOW,
                created_by="u-priya",
                field_values={"F01": "x"},
            ),
            SubmissionVersionRecord(
                version=2,
                created_at=NOW,
                created_by="u-priya",
                field_values={"F01": "y"},
            ),
        ]
    )
    analyst = to_case_detail_analyst(record)
    assert [v.version for v in analyst.submission_versions] == [1, 2]
    assert analyst.submission_versions[0].diff[0].before is None
    assert analyst.submission_versions[0].diff[0].after == "x"
    assert analyst.submission_versions[1].diff[0].before == "x"
    assert analyst.submission_versions[1].diff[0].after == "y"
