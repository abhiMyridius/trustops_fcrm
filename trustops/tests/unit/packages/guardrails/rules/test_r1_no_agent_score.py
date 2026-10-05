"""Rule 1: no agent wrote a score (schema and payload checks)."""

from __future__ import annotations

from typing import Optional

import pytest
from fcrm_contracts.enums import Domain
from fcrm_contracts.proposals.assessment import ControlProposal, FactorProposal
from fcrm_contracts.proposals.provenance import ControlSourceRef
from fcrm_guardrails.output_shape import PROPOSAL_TYPES
from fcrm_guardrails.rules.r1_no_agent_score import (
    BANNED_FIELD_NAMES,
    banned_schema_fields,
    check,
    is_banned_name,
)
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict, create_model

FACTOR = FactorProposal(
    source_ref=ControlSourceRef(control_id="CTL-T01", controls_v=9),
    quote="vendor due diligence on the payout partner",
    confidence=0.8,
    domain=Domain.CHANNEL,
    likelihood=4,
    impact=5,
    justification="third-party payout network",
)
CONTROL = ControlProposal(
    source_ref=ControlSourceRef(control_id="CTL-T01", controls_v=9),
    quote="2023 audit of domestic operations",
    confidence=0.6,
    control_id="CTL-T01",
    domain=Domain.CHANNEL,
    rating=7.6,
    rationale="2023 domestic-scope audit; no corridor-specific review yet",
    evidence_artifact="audit_2023.pdf",
    evidence_date="2023-06-30",
)

ALLOWED_NAME = st.from_regex(r"[a-z]{3,12}(_[a-z]{3,8})?", fullmatch=True).filter(
    lambda n: not is_banned_name(n)
)


class _Inner(BaseModel):
    model_config = ConfigDict(extra="forbid")
    composite: float


class _Outer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Optional[_Inner]]


@pytest.mark.parametrize("model", sorted(PROPOSAL_TYPES, key=lambda m: m.__name__))
def test_check_every_contract_proposal_schema_is_clean(model: type[BaseModel]) -> None:
    assert check(model).ok


def test_check_factor_and_control_inputs_are_allowed() -> None:
    result = check([FACTOR, CONTROL])
    assert result.ok
    assert result.rule_id == "R-1"


def test_check_fails_when_a_proposal_model_grows_a_score_field() -> None:
    class ScoredFactor(FactorProposal):
        score: Optional[float] = None

    result = check(ScoredFactor)
    assert not result.ok
    assert "ScoredFactor.score" in result.detail


def test_check_finds_banned_fields_in_nested_models() -> None:
    assert banned_schema_fields(_Outer) == ["_Inner.composite"]


def test_check_finds_banned_keys_in_raw_payloads() -> None:
    payload = {"proposals": [{"domain": "channel", "detail": {"residual_score": 12.2}}]}
    result = check(payload)
    assert not result.ok
    assert "$.proposals[0].detail.residual_score" in result.detail


@pytest.mark.parametrize(
    "name", ["score", "Band", "composite_score", "risk_score", "final_band", "route"]
)
def test_is_banned_name_true(name: str) -> None:
    assert is_banned_name(name)


@pytest.mark.parametrize(
    "name", ["likelihood", "impact", "rating", "similarity", "scope"]
)
def test_is_banned_name_false(name: str) -> None:
    assert not is_banned_name(name)


@given(ALLOWED_NAME)
def test_property_models_without_banned_names_pass(name: str) -> None:
    model = create_model(
        "Generated", __config__=ConfigDict(extra="forbid"), **{name: (int, 0)}
    )
    assert check(model).ok


@given(st.sampled_from(sorted(BANNED_FIELD_NAMES)), ALLOWED_NAME)
def test_property_any_banned_field_fails_schema_and_payload(
    banned: str, other: str
) -> None:
    model = create_model(
        "Generated",
        __config__=ConfigDict(extra="forbid"),
        **{other: (int, 0), banned: (Optional[float], None)},
    )
    assert not check(model).ok
    assert not check(model()).ok
    assert not check({other: 1, banned: None}).ok
