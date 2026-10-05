"""``fcrm_case.shaping``: mapping one stored ``CaseRecord`` onto the analyst and
internal wire contracts. (The product owner's shapes are built in
``fcrm_case.po_views`` and fenced by ``test_po_models_forbidden_fields.py``,
``test_po_router_imports.py`` and ``test_po_guard.py``.)
"""

from __future__ import annotations

from datetime import UTC, datetime

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
    to_case_list_item_analyst,
)
from fcrm_contracts.api.case_internal import CaseDetailInternal
from fcrm_contracts.enums import CaseState, ChangeType
from fcrm_contracts.versions import PinnedVersions

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


def test_the_analyst_list_item_carries_the_assignment() -> None:
    record = _record()
    item = to_case_list_item_analyst(record)
    assert item.case_id == record.case_id
    assert item.analyst_id == "u-rahul"


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
