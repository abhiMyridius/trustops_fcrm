"""Rule 7: a planned control never reaches the weighted set. Uses the demo
case's own score inputs: after the override, the channel domain lists
CTL-T01 twice, once rated 4.5 and once planned with no rating (the
scheduled corridor review behind condition CND-2's "planned control")."""

from __future__ import annotations

import pytest
from fcrm_contracts.api.scoring import ControlInput, DomainInput, ScoreRequest
from fcrm_contracts.enums import Domain
from fcrm_guardrails.rules.r7_planned_not_scored import check
from fcrm_seed.loader import demo_case
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

AREAS = demo_case()["score_after"]["areas"]
CHANNEL = AREAS["channel"]["controls"]
DEMO_CONTROLS = [c for area in AREAS.values() for c in area["controls"]]

CONTROLS = st.lists(
    st.fixed_dictionaries(
        {
            "id": st.from_regex(r"CTL-[A-Z]\d{2}", fullmatch=True),
            "rating": st.floats(0, 10),
            "planned": st.booleans(),
        }
    ),
    max_size=12,
    unique_by=lambda c: c["id"],
)


def _request(*control_ids: str) -> ScoreRequest:
    return ScoreRequest(
        case_id="FCRM-2026-0151",
        scoring_v=3,
        domains=[
            DomainInput(
                domain=Domain.CHANNEL,
                likelihood=4,
                impact=5,
                controls=[
                    ControlInput(
                        control_id=cid,
                        domain=Domain.CHANNEL,
                        rating=6.0,
                        catalogue_weight=0.35,
                    )
                    for cid in control_ids
                ],
            )
        ],
    )


def test_demo_planned_entry_shares_the_id_of_a_scored_control() -> None:
    planned = [c for c in CHANNEL if c.get("planned")]
    assert [(c["id"], c["rating"]) for c in planned] == [("CTL-T01", None)]
    assert any(c["id"] == "CTL-T01" and not c.get("planned") for c in CHANNEL)


def test_check_raw_demo_controls_fail() -> None:
    result = check(DEMO_CONTROLS)
    assert not result.ok
    assert "CTL-T01" in result.detail


def test_check_demo_controls_filtered_like_the_seedmodel_pass() -> None:
    assert check([c for c in DEMO_CONTROLS if not c.get("planned")]).ok


def test_check_unrated_control_counts_as_planned() -> None:
    assert not check([{"id": "CTL-T05", "rating": None}]).ok


def test_check_score_request_with_duplicated_control_fails() -> None:
    """ControlInput has no planned flag: a planned CTL-T01 that slipped
    through shows up as CTL-T01 listed twice in one domain.

    ``ScoreRequest`` itself now refuses a duplicated control id at
    validation (``fcrm_contracts.api.scoring``), so a validated request can
    never reach this rule with one; the rule stays as defence in depth for
    an instance built without validation, which is what this test builds.
    """
    valid = _request("CTL-T01", "CTL-T02")
    domain = valid.domains[0]
    duplicated = DomainInput.model_construct(
        domain=domain.domain,
        likelihood=domain.likelihood,
        impact=domain.impact,
        controls=[*domain.controls, domain.controls[0]],
    )
    unvalidated = ScoreRequest.model_construct(
        case_id=valid.case_id, scoring_v=valid.scoring_v, domains=[duplicated]
    )
    result = check(unvalidated)
    assert not result.ok
    assert "CTL-T01 (listed 2x)" in result.detail


def test_score_request_contract_refuses_a_duplicated_control_before_rule_7() -> None:
    with pytest.raises(ValidationError, match="CTL-T01"):
        _request("CTL-T01", "CTL-T02", "CTL-T01")


def test_check_score_request_clean_passes() -> None:
    assert check(_request("CTL-T01", "CTL-T02", "CTL-T03", "CTL-T04")).ok


def test_check_planned_only_ids_are_refused() -> None:
    request = _request("CTL-T01", "CTL-T09")
    assert check(request).ok
    assert not check(request, planned_only_ids=["CTL-T09"]).ok


@given(CONTROLS)
def test_property_fails_iff_any_control_is_planned(
    controls: list[dict[str, object]],
) -> None:
    assert check(controls).ok is (not any(c["planned"] for c in controls))


@given(CONTROLS)
def test_property_filtering_planned_always_passes(
    controls: list[dict[str, object]],
) -> None:
    assert check([c for c in controls if not c["planned"]]).ok
