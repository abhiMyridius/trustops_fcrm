"""The tool schema a model sees: compact, and without the arguments the job
fills in (the case, the run, the pinned versions, the search index). A model
asked for them names the wrong ones; the job fixes them instead of refusing
the call afterwards."""

from __future__ import annotations

from typing import Optional

from fcrm_agents.runtime import (
    AgentDeps,
    StaticPermitClient,
    compact_schema,
    model_schema,
    scope_values,
)
from fcrm_contracts.api.policy import SearchRequest
from fcrm_contracts.tools.audit_log import AuditLogInput
from fcrm_contracts.tools.control_inventory import ControlInventoryInput
from fcrm_contracts.tools.evidence_ledger import EvidenceLedgerInput
from fcrm_contracts.tools.field_rules import FieldRulesInput
from fcrm_guardrails import PermitGuard
from fcrm_seed.loader import load_raw
from fcrm_tools import DEMO_CASE_ID, DEMO_RUN_ID, build_seed_registry, seed_pins
from pydantic import BaseModel, Field


class _WithATitleField(BaseModel):
    title: str = Field(description="what the case is called")
    note: Optional[str] = None


def _deps() -> AgentDeps:
    return AgentDeps(
        case_id=DEMO_CASE_ID,
        run_id=DEMO_RUN_ID,
        agent="research",
        registry=build_seed_registry(),
        permit=PermitGuard(StaticPermitClient.from_registry()),
        versions=seed_pins(load_raw()),
    )


def test_compact_schema_drops_title_keywords_and_null_defaults() -> None:
    schema = {
        "title": "Thing",
        "properties": {"a": {"title": "A", "type": "string", "default": None}},
    }
    assert compact_schema(schema) == {"properties": {"a": {"type": "string"}}}


def test_compact_schema_keeps_a_property_that_is_called_title() -> None:
    compact = compact_schema(_WithATitleField.model_json_schema())
    assert "title" in compact["properties"]
    assert compact["properties"]["title"]["description"] == "what the case is called"


def test_the_search_index_is_the_tools_not_the_models_to_choose() -> None:
    for tool in ("policy_search", "precedent_search"):
        schema = model_schema(tool, SearchRequest)
        assert "index" not in schema["properties"]
        assert "index" not in schema["required"]
        assert "query" in schema["properties"]


def test_the_case_and_run_are_not_arguments_the_model_supplies() -> None:
    audit = model_schema("audit_log", AuditLogInput)
    assert "case_id" not in audit["properties"]
    assert "seq_from" in audit["properties"]
    ledger = model_schema("evidence_ledger", EvidenceLedgerInput)
    assert {"case_id", "run_id"}.isdisjoint(ledger["properties"])
    assert "agent" in ledger["properties"]


def test_the_pinned_versions_are_not_arguments_the_model_supplies() -> None:
    controls = model_schema("control_inventory", ControlInventoryInput)
    assert "controls_v" not in controls["properties"]
    assert controls["required"] == ["domain"]
    rules = model_schema("field_rules", FieldRulesInput)
    assert "fieldset_v" not in rules["properties"]


def test_a_tool_with_nothing_to_hide_keeps_every_property() -> None:
    schema = model_schema("rules_check", _WithATitleField)
    assert set(schema["properties"]) == {"title", "note"}
    assert schema["required"] == ["title"]


def test_scope_values_come_from_the_job() -> None:
    deps = _deps()
    values = scope_values("policy_search", deps)
    assert values["case_id"] == DEMO_CASE_ID
    assert values["run_id"] == DEMO_RUN_ID
    assert values["controls_v"] == deps.versions.controls_v
    assert values["fieldset_v"] == deps.versions.fieldset_v
    assert values["index"] == "policy_clauses"
    assert scope_values("precedent_search", deps)["index"] == "precedent_cases"
    assert "index" not in scope_values("audit_log", deps)
