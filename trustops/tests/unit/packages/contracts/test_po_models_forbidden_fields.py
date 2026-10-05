"""The PO contract's first fence (D-62, rule 3): no model in
``fcrm_contracts.api.case_po`` has a field that could carry a score, band,
rating, clause, critic finding, vote, pack or any harness data.

A schema lock: it reads the declared fields (and every nested JSON-schema
property), not any instance, so a field added later (``band_hint``,
``model_id``) fails here before it is ever populated with real data. The names
come from design A section 2.1 (D-62) and from the restricted list rule 3 scans
responses against (``fcrm_guardrails.restricted_terms``); the test also checks
that list still covers D-62, so the scanner cannot quietly shrink.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from fcrm_contracts.api import case_po
from fcrm_guardrails.restricted_terms import RESTRICTED_KEYS
from fcrm_guardrails.rules.r3_po_no_restricted import key_tokens, restricted_key
from pydantic import BaseModel

#: Design A section 2.1, D-62: the keys no PO model or response may contain.
D62_KEYS = frozenset(
    {
        "score",
        "composite",
        "band",
        "route",
        "residual",
        "inherent",
        "retention",
        "ce",
        "control_effectiveness",
        "rating",
        "likelihood",
        "impact",
        "clause",
        "clauses",
        "boundary",
        "critic",
        "precedent_rating",
        "recommendation",
        "vote",
        "pack",
    }
)

#: Further names that belong to the harness, the analyst or the audit trail and
#: so never to a PO shape (BUILD-PLAN section 3.7: no harness data either).
EXTRA_FORBIDDEN = frozenset(
    {
        "scores",
        "ratings",
        "votes",
        "harness",
        "pack_hash",
        "prompt_v",
        "prompt_version",
        "model_id",
        "guard_results",
        "tokens",
        "token_count",
        "cost",
        "agent",
        "model",
        "prompt",
        "run_id",
        "job_id",
        "step_id",
        "trace_id",
        "gate_id",
        "proposal_id",
        "similarity",
        "outcome",
        "analyst_id",
        "record_seq",
    }
)


def po_models() -> list[type[BaseModel]]:
    return [
        model
        for _, model in inspect.getmembers(case_po, inspect.isclass)
        if issubclass(model, BaseModel)
        and model is not BaseModel
        and model.__module__ == case_po.__name__
    ]


def schema_property_names(schema: Any) -> set[str]:
    """Every property name anywhere in a JSON schema (nested models included)."""
    found: set[str] = set()
    if isinstance(schema, dict):
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                found.update(value)
            found.update(schema_property_names(value))
    elif isinstance(schema, list):
        for item in schema:
            found.update(schema_property_names(item))
    return found


def test_the_guard_list_still_covers_d62() -> None:
    assert D62_KEYS <= RESTRICTED_KEYS


def test_there_are_models_to_lock() -> None:
    assert len(po_models()) > 30


@pytest.mark.parametrize("model", po_models(), ids=lambda m: m.__name__)
def test_no_field_of_a_po_model_is_on_the_d62_list(model: type[BaseModel]) -> None:
    names = set(model.model_fields) | {
        info.alias for info in model.model_fields.values() if info.alias
    }
    assert names.isdisjoint(D62_KEYS | EXTRA_FORBIDDEN), (
        f"{model.__name__}: {sorted(names & (D62_KEYS | EXTRA_FORBIDDEN))}"
    )
    # The scanner's own rule (whole key, each token, every run of tokens): a
    # name like ``band_hint`` or ``composite_value`` is caught too.
    scanned = sorted(n for n in names if restricted_key(n))
    assert scanned == [], f"{model.__name__}: {scanned}"


@pytest.mark.parametrize("model", po_models(), ids=lambda m: m.__name__)
def test_no_property_of_a_po_json_schema_is_forbidden(model: type[BaseModel]) -> None:
    properties = schema_property_names(model.model_json_schema(mode="serialization"))
    assert properties.isdisjoint(D62_KEYS | EXTRA_FORBIDDEN)
    assert [p for p in properties if restricted_key(p)] == []


@pytest.mark.parametrize("model", po_models(), ids=lambda m: m.__name__)
def test_every_po_model_forbids_unknown_fields(model: type[BaseModel]) -> None:
    """A smuggled ``score`` in a request or a built response is a validation
    error, never a silently carried extra.
    """
    assert model.model_config.get("extra") == "forbid"
    assert model.model_json_schema().get("additionalProperties") is False


def test_the_lock_is_not_vacuous() -> None:
    class Leaky(BaseModel):
        band_hint: str

    assert restricted_key("band_hint")
    assert key_tokens("band_hint") == ["band", "hint"]
    assert {"band_hint"} & D62_KEYS == set()  # caught by the token rule, not the list
    assert [n for n in Leaky.model_fields if restricted_key(n)] == ["band_hint"]
