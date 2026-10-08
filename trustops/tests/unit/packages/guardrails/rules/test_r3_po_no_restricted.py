"""Rule 3: the PO view carries no score, band, rating or harness data.
The clean fixtures are the demo case's real PO-facing strings."""

from __future__ import annotations

import pytest
from fcrm_contracts.api.case_po import (
    CasePO,
    Completion,
    ConditionPO,
    OutcomePO,
    StageStep,
)
from fcrm_contracts.enums import ChangeType, Role
from fcrm_guardrails.rules.r3_po_no_restricted import (
    check,
    find_restricted,
    key_tokens,
    restricted_key,
    restricted_terms_in,
)
from fcrm_seed.loader import demo_case

DEMO = demo_case()

PO_DETAIL = CasePO(
    id=DEMO["id"],
    title=DEMO["title"],
    unit=DEMO["unit"],
    owner_name="Priya Nair",
    change_types=[
        ChangeType.VENDOR_ONBOARDING,
        ChangeType.NEW_GEOGRAPHY,
        ChangeType.NEW_PRODUCT,
    ],
    stage="closed",
    stage_label="Closed",
    lifecycle=[
        StageStep(key=key, label=label, state="done")
        for key, label in (
            ("draft", "Draft"),
            ("submitted", "Submitted"),
            ("under_assessment", "Under assessment"),
            ("decision", "Decision"),
            ("closed", "Closed"),
        )
    ],
    created="2026-10-01",
    submitted="2026-10-01",
    analyst_name="Rahul Iyer",
    next_action="3 open conditions",
    completion=Completion(
        filled=18,
        required=18,
        pct=100,
        threshold_pct=80,
        always_required_filled=6,
        always_required_total=6,
    ),
    closed=True,
    inbox_open=0,
    intake_mode="ai_assisted",
)

PO_OUTCOME = OutcomePO(
    decided=True,
    outcome_label="Approved with conditions",
    plain_summary=(
        "Approved with conditions. Three conditions apply before and after go-live."
    ),
    decided_at="2026-10-03T12:00:00Z",
    conditions=[
        ConditionPO(
            id=c["id"],
            text=c["text"],
            owner=c["owner"],
            due=c["due"],
            status="open",
            mine=False,
        )
        for c in DEMO["conditions"]
    ],
    reminder_days=14,
)


def test_check_demo_po_case_and_outcome_are_clean() -> None:
    for body in (PO_DETAIL, PO_OUTCOME):
        result = check(body, Role.PO)
        assert result.ok, result.detail


def test_check_po_contract_schema_has_no_restricted_keys() -> None:
    for model in (CasePO, OutcomePO):
        assert check(dict.fromkeys(model.model_fields, ""), Role.PO).ok


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


@pytest.mark.parametrize(
    ("text", "term"),
    [
        ("Residual risk sits in Band 3", "band"),  # NBSP
        ("Residual risk sits in Band\n3", "band"),  # newline between words
        ("Residual risk sits in Band    3", "band"),  # run of spaces
        ("sits in Ba​nd 3", "band"),  # zero-width space inside the word
        ("sits in B‍and 3", "band"),  # zero-width joiner
        ("sits in ‮Band 3", "band"),  # bidi override before the word
        ("Ｂａｎｄ 3", "band"),  # fullwidth letters
        ("Bаnd 3", "band"),  # Cyrillic а
        ("Сomposite 10.4", "composite"),  # Cyrillic С
        ("Band2 committee", "band"),  # digit glued to the word
        ("the score:9.8 moved", "score"),
        ("inherent risk is high", "inherent_risk"),
        ("retention\nfactor applied", "retention"),
        ("see prompt_v2 and run_id", "harness_id"),
        ("the model_id was pinned", "harness_id"),
    ],
)
def test_restricted_terms_in_sees_through_obfuscation(text: str, term: str) -> None:
    assert term in restricted_terms_in(text)


@pytest.mark.parametrize(
    "text",
    [
        "bandwidth for the payout partner is sufficient",
        "broadband access is required at every agent location",
        "the vendor provided a scorecard template in the RFP",
        "abandon the pilot if the corridor volume stays low",
        "husband-and-wife owned agent locations are common",
        "critical path items are listed below",  # 'critic' is letter-bounded
        "the composition of the agent network",  # not 'composite'
        "customer retention improved after onboarding",  # not 'retention factor'
        "Approved with conditions. Three conditions apply before and after go-live.",
    ],
)
def test_restricted_terms_in_leaves_benign_po_text_alone(text: str) -> None:
    assert restricted_terms_in(text) == []


def test_demo_po_strings_stay_clean_under_the_normalising_scanner() -> None:
    """Every PO-facing string in the demo (field values, condition texts)
    must still pass after NFKC/homoglyph/whitespace normalisation."""
    texts = [f["value"] for f in DEMO["fields"] if f.get("value")]
    texts += [c["text"] for c in DEMO["conditions"]]
    assert texts
    for text in texts:
        assert restricted_terms_in(text) == [], text


@pytest.mark.parametrize(
    "key", ["modelId", "promptV", "prompt_version", "band2", "promptV2", "Band​"]
)
def test_restricted_key_catches_camel_digit_and_zero_width_variants(key: str) -> None:
    assert restricted_key(key)


def test_key_tokens_split_at_digit_boundaries() -> None:
    assert key_tokens("band2") == ["band", "2"]
    assert key_tokens("promptV2") == ["prompt", "v", "2"]


def test_check_reports_an_obfuscated_value_hit_with_its_path() -> None:
    result = check({"note": "sits in Ba​nd 3"}, Role.PO)
    assert not result.ok
    assert "value 'band' at $.note" in result.detail
