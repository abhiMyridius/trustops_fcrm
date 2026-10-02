"""Rule 3: the PO view carries no score, band, rating or harness data.
The clean fixtures are the demo case's real PO-facing strings."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fcrm_contracts.api.case_po import CaseDetailPO, ConditionPO, FieldValuePO
from fcrm_contracts.enums import CaseState, ChangeType, Role
from fcrm_guardrails.rules.r3_po_no_restricted import (
    check,
    find_restricted,
    key_tokens,
    restricted_key,
)
from fcrm_seed.loader import demo_case

DEMO = demo_case()
AT = datetime(2026, 10, 3, tzinfo=UTC)

PO_DETAIL = CaseDetailPO(
    case_id=DEMO["id"],
    title=DEMO["title"],
    state=CaseState.CLOSED,
    unit=DEMO["unit"],
    owner=DEMO["owner"],
    change_types=[
        ChangeType.VENDOR_ONBOARDING,
        ChangeType.NEW_GEOGRAPHY,
        ChangeType.NEW_PRODUCT,
    ],
    completion_pct=100.0,
    threshold_pct=80.0,
    required_field_count=18,
    required_fields_filled=18,
    fields=[
        FieldValuePO(
            field_id=f["id"],
            label=f["id"],
            value=f["value"],
            source=f.get("source"),
            how=f.get("how"),
        )
        for f in DEMO["fields"]
    ],
    documents=[],
    clarifications=[],
    conditions=[
        ConditionPO(
            condition_id=c["id"],
            text=c["text"],
            owner=c["owner"],
            due=c["due"],
            status=c["status"],
        )
        for c in DEMO["conditions"]
    ],
    outcome_summary=(
        "Approved with conditions. Three conditions apply before and after go-live."
    ),
    created_at=AT,
)


def test_check_demo_po_case_detail_is_clean() -> None:
    result = check(PO_DETAIL, Role.PO)
    assert result.ok, result.detail


def test_check_po_contract_schema_has_no_restricted_keys() -> None:
    assert check(dict.fromkeys(CaseDetailPO.model_fields, ""), Role.PO).ok


@pytest.mark.parametrize(
    ("payload", "term"),
    [
        ({"score": 9.8}, "score"),
        ({"summary": {"compositeScore": 10.4}}, "compositeScore"),
        ({"bandLabel": "Band 3"}, "bandLabel"),
        ({"items": [{"pack_hash": "abc"}]}, "pack_hash"),
        ({"note": "Residual risk sits in Band 3"}, "band"),
        ({"note": "vendor DD rating was overridden"}, "rating"),
        ({"note": "condition from overridden control CTL-T01"}, "control_id"),
        ({"note": "the critic flagged this"}, "critic"),
        ({"note": ["fine", ("nested", "boundary flag raised")]}, "boundary_flag"),
    ],
)
def test_check_restricted_keys_and_terms_fail(payload: object, term: str) -> None:
    result = check(payload, Role.PO)
    assert not result.ok
    assert term in result.detail


def test_check_analyst_responses_are_not_this_rules_concern() -> None:
    result = check({"score": 9.8}, Role.ANALYST)
    assert result.ok
    assert not result.applicable


def test_check_detail_names_path_not_value() -> None:
    result = check({"conditions": [{"text": "Band 3 committee"}]}, Role.PO)
    assert "$.conditions[0].text" in result.detail
    assert "committee" not in result.detail


def test_check_recommendation_text_from_the_seed_is_restricted() -> None:
    hits = find_restricted({"recommendation_text": DEMO["recommendation"]["text"]})
    assert {h.term for h in hits} >= {"recommendation_text", "band", "residual"}


def test_key_tokens_split_snake_and_camel() -> None:
    assert key_tokens("compositeScore") == ["composite", "score"]
    assert key_tokens("pack_hash") == ["pack", "hash"]


@pytest.mark.parametrize(
    "key", ["source", "threshold_pct", "completion_pct", "round", "due"]
)
def test_restricted_key_po_contract_keys_are_allowed(key: str) -> None:
    assert not restricted_key(key)


def test_check_many_hits_are_truncated_in_detail() -> None:
    result = check({f"score_{n}": n for n in range(15)}, Role.PO)
    assert "(+5 more)" in result.detail
