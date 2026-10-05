"""The pure derivations over S1's review items: the S2 request for the case as
it stands (:func:`fcrm_case.domain.assessment.build_score_view`) and the evidence
a control holds. The score request is checked against the real engine and the
walkthrough's goldens: S1's items, built from the seed's ``score_before`` inputs,
score 9.8 Band 2, and overriding CTL-T01 7.6 to 4.5 scores 10.4 Band 3.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fcrm_case.domain.assessment import (
    build_score_view,
    control_evidence,
    current_items,
)
from fcrm_case.domain.views import ReviewItemRow
from fcrm_common.errors import ConflictError
from fcrm_contracts.enums import Band, ItemKind, ItemStatus
from fcrm_scoring.engine import compute_score
from fcrm_scoring.seedmodel import scoring_model_from_seed

CASE = "FCRM-2026-0151"


def _control(rows: list[ReviewItemRow], control_id: str, planned: bool = False) -> int:
    for index, row in enumerate(rows):
        if (
            row.kind is ItemKind.CONTROL
            and row.subject["control_id"] == control_id
            and row.subject["planned"] is planned
        ):
            return index
    raise AssertionError(control_id)


def _overridden(row: ReviewItemRow, rating: float) -> ReviewItemRow:
    from fcrm_case.domain.views import DecisionRow

    return row.model_copy(
        update={
            "status": ItemStatus.OVERRIDDEN,
            "decided": DecisionRow(
                value={"rating": rating},
                reason="Audit predates the corridor arrangement.",
                by="u-riyer",
                at=row.created_at,
            ),
        }
    )


def _score(
    view_request: Any, seeded_scoring_model: dict[str, Any]
) -> tuple[float, Band, bool]:
    response = compute_score(
        view_request, scoring_model_from_seed(seeded_scoring_model)
    )
    return response.composite, response.band, response.boundary_flag


def test_the_demo_items_score_9_8_band_2_with_a_boundary_flag(
    demo_review_items: list[ReviewItemRow], seeded_scoring_model: dict[str, Any]
) -> None:
    view = build_score_view(CASE, 3, demo_review_items)

    assert _score(view.request, seeded_scoring_model) == (9.8, Band.BAND_2, True)
    assert [d.domain.value for d in view.request.domains] == [
        "channel",
        "geography",
        "product",
    ]
    assert view.overrides == []


def test_a_planned_control_is_never_in_the_request(
    demo_review_items: list[ReviewItemRow],
) -> None:
    view = build_score_view(CASE, 3, demo_review_items)

    channel = next(d for d in view.request.domains if d.domain.value == "channel")
    ids = [c.control_id for c in channel.controls]
    assert ids.count("CTL-T01") == 1  # the scored instance only
    assert view.planned_control_ids == ["CTL-T01"]
    assert all(c.rating is not None for d in view.request.domains for c in d.controls)


def test_overriding_ctl_t01_to_4_5_scores_10_4_band_3(
    demo_review_items: list[ReviewItemRow], seeded_scoring_model: dict[str, Any]
) -> None:
    rows = list(demo_review_items)
    index = _control(rows, "CTL-T01")
    rows[index] = _overridden(rows[index], 4.5)

    view = build_score_view(CASE, 3, rows)

    assert _score(view.request, seeded_scoring_model) == (10.4, Band.BAND_3, True)
    assert [(o.subject_id, o.proposed, o.decided) for o in view.overrides] == [
        ("CTL-T01", {"rating": 7.6}, {"rating": 4.5})
    ]


def test_a_removed_control_leaves_the_weighted_set(
    demo_review_items: list[ReviewItemRow],
) -> None:
    rows = list(demo_review_items)
    index = _control(rows, "CTL-T02")
    rows[index] = rows[index].model_copy(update={"status": ItemStatus.REMOVED})

    view = build_score_view(CASE, 3, rows)

    channel = next(d for d in view.request.domains if d.domain.value == "channel")
    assert "CTL-T02" not in [c.control_id for c in channel.controls]


def test_a_superseded_item_is_not_current(
    demo_review_items: list[ReviewItemRow],
    make_review_item: Callable[..., ReviewItemRow],
) -> None:
    rows = list(demo_review_items)
    old = rows[_control(rows, "CTL-G02")]
    newer = make_review_item(
        ItemKind.CONTROL,
        {**old.subject},
        {**old.proposed, "rating": 6.0},
        supersedes=old.item_id,
        revision="r2",
    )
    rows.append(newer)

    assert old.item_id not in {r.item_id for r in current_items(rows)}
    view = build_score_view(CASE, 3, rows)
    geography = next(d for d in view.request.domains if d.domain.value == "geography")
    assert [c.rating for c in geography.controls if c.control_id == "CTL-G02"] == [6.0]


def test_a_factor_adjustment_replaces_likelihood_and_impact(
    demo_review_items: list[ReviewItemRow],
) -> None:
    from fcrm_case.domain.views import DecisionRow

    rows = list(demo_review_items)
    index = next(i for i, r in enumerate(rows) if r.kind is ItemKind.FACTOR)
    rows[index] = rows[index].model_copy(
        update={
            "status": ItemStatus.OVERRIDDEN,
            "decided": DecisionRow(
                value={"likelihood": 5, "impact": 5},
                reason="Volume is higher than stated.",
                by="u-riyer",
                at=rows[index].created_at,
            ),
        }
    )

    view = build_score_view(CASE, 3, rows)

    first = view.request.domains[0]
    assert (first.likelihood, first.impact) == (5, 5)
    assert view.overrides[0].kind == "factor"


def test_a_control_without_a_factor_in_its_domain_cannot_be_scored(
    demo_review_items: list[ReviewItemRow],
    make_review_item: Callable[..., ReviewItemRow],
) -> None:
    rows = [*demo_review_items]
    rows.append(
        make_review_item(
            ItemKind.CONTROL,
            {
                "control_id": "CTL-C01",
                "domain": "customer",
                "planned": False,
                "catalogue_weight": 0.18,
                "controls_v": 9,
            },
            {
                "rating": 5.0,
                "rationale": "r",
                "evidence_artifact": "a",
                "evidence_date": "2026-01-01",
                "quote": "q",
                "confidence": 0.7,
            },
        )
    )

    view = build_score_view(CASE, 3, rows)

    assert view.unscored_control_ids == ["CTL-C01"]
    assert "customer" not in [d.domain.value for d in view.request.domains]


def test_a_case_with_no_factor_items_has_nothing_to_score() -> None:
    with pytest.raises(ConflictError):
        build_score_view(CASE, 3, [])


def test_an_item_that_breaks_its_kinds_shape_is_refused_by_name(
    make_review_item: Callable[..., ReviewItemRow],
) -> None:
    bad = make_review_item(ItemKind.FACTOR, {"domain": "channel"}, {"likelihood": 9})

    with pytest.raises(ConflictError, match=bad.item_id):
        build_score_view(CASE, 3, [bad])


def test_a_scored_control_with_no_rating_is_refused_not_scored_as_zero(
    demo_review_items: list[ReviewItemRow],
) -> None:
    rows = list(demo_review_items)
    index = _control(rows, "CTL-T02")
    rows[index] = rows[index].model_copy(
        update={"proposed": {**rows[index].proposed, "rating": None}}
    )

    with pytest.raises(ConflictError, match="has no rating"):
        build_score_view(CASE, 3, rows)


def test_undecided_items_are_counted(
    demo_review_items: list[ReviewItemRow],
) -> None:
    view = build_score_view(CASE, 3, demo_review_items)

    # three factors, thirteen scored controls, one planned control: all proposed
    assert view.undecided_items == 3 + 13 + 1


def test_a_missing_catalogue_weight_is_a_malformed_control_item(
    demo_review_items: list[ReviewItemRow],
) -> None:
    rows = list(demo_review_items)
    index = _control(rows, "CTL-T03")
    subject = {k: v for k, v in rows[index].subject.items() if k != "catalogue_weight"}
    rows[index] = rows[index].model_copy(update={"subject": subject})

    with pytest.raises(ConflictError, match="ControlSubject"):
        build_score_view(CASE, 3, rows)


# ── evidence ────────────────────────────────────────────────────────────────


def test_evidence_lists_each_distinct_artefact_for_the_control(
    demo_review_items: list[ReviewItemRow],
) -> None:
    out = control_evidence(CASE, "CTL-T01", demo_review_items)

    assert out.control_id == "CTL-T01"
    assert [(i.artifact, i.evidence_date) for i in out.items] == [
        ("vendor_DD_scope_letter_2026.pdf", ""),
        ("DD report, audit 2023-11, domestic scope", "2023-11-15"),
    ]


def test_evidence_for_an_unassessed_control_is_empty(
    demo_review_items: list[ReviewItemRow],
) -> None:
    assert control_evidence(CASE, "CTL-X99", demo_review_items).items == []


def test_evidence_names_the_document_an_item_cites(
    make_review_item: Callable[..., ReviewItemRow],
) -> None:
    row = make_review_item(
        ItemKind.CONTROL,
        {"control_id": "CTL-T01", "domain": "channel", "planned": True},
        {"evidence_artifact": "scope letter", "evidence_date": "2026-09-01"},
    ).model_copy(
        update={
            "source_refs": [
                {"kind": "doc", "doc_id": "doc_dd", "page": 1, "line_start": 2}
            ]
        }
    )

    out = control_evidence(CASE, "CTL-T01", [row])

    assert out.items[0].doc_id == "doc_dd"


def test_evidence_refuses_another_cases_rows(
    make_review_item: Callable[..., ReviewItemRow],
) -> None:
    row = make_review_item(
        ItemKind.CONTROL,
        {"control_id": "CTL-T01"},
        {"evidence_artifact": "a", "evidence_date": "d"},
        case_id="FCRM-2026-0001",
    )

    with pytest.raises(ConflictError):
        control_evidence(CASE, "CTL-T01", [row])
