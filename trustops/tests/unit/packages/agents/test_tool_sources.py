"""``fcrm_agents.tool_sources``: what each tool result lets an agent cite. The
sources carry the refs and the text a pack builder would have given the same
facts, so a quote verifies against the one string either way."""

from __future__ import annotations

from fcrm_agents.builders import FATF_LIST_ID
from fcrm_agents.tool_sources import sources_from_result
from fcrm_contracts.api.policy import SearchFilters, SearchRequest
from fcrm_contracts.proposals.provenance import (
    CaseSourceRef,
    ClauseSourceRef,
    ControlSourceRef,
    ListSourceRef,
    RecordSourceRef,
)
from fcrm_contracts.tools.audit_log import AuditLogInput
from fcrm_contracts.tools.case_record import CaseRecordInput
from fcrm_contracts.tools.control_inventory import ControlInventoryInput
from fcrm_contracts.tools.field_rules import FieldRulesInput
from fcrm_contracts.tools.jurisdiction_list import JurisdictionListInput
from fcrm_contracts.versions import PinnedVersions
from fcrm_guardrails.context import AvailableSource
from fcrm_seed.loader import load_raw
from fcrm_tools import DEMO_CASE_ID, build_seed_registry, seed_pins
from pydantic import BaseModel

VERSIONS: PinnedVersions = seed_pins(load_raw())


async def _sources(tool: str, request: BaseModel) -> list[AvailableSource]:
    registry = build_seed_registry()
    result = await registry.get(tool).call(request)
    return sources_from_result(
        tool, request, result, case_id=DEMO_CASE_ID, versions=VERSIONS
    )


async def test_policy_search_yields_only_the_quotable_clauses() -> None:
    request = SearchRequest(
        index="policy_clauses",
        query="cash pickup agent network",
        top_k=3,
        filters=SearchFilters(corpus_version=14),
    )
    sources = await _sources("policy_search", request)
    refs = [s.source_ref for s in sources]
    assert refs
    assert all(isinstance(r, ClauseSourceRef) for r in refs)
    assert {getattr(r, "corpus_v", None) for r in refs} == {14}
    assert all(s.text for s in sources)


async def test_policy_search_names_the_pinned_corpus_when_the_call_did_not() -> None:
    request = SearchRequest(index="policy_clauses", query="payout agents", top_k=3)
    sources = await _sources("policy_search", request)
    refs = [s.source_ref for s in sources]
    assert {getattr(r, "corpus_v", None) for r in refs} == {VERSIONS.corpus_v}


async def test_precedent_search_yields_the_cases_it_returned() -> None:
    request = SearchRequest(
        index="precedent_cases", query="payout partner Mexico", top_k=2
    )
    sources = await _sources("precedent_search", request)
    assert sources
    assert all(isinstance(s.source_ref, CaseSourceRef) for s in sources)
    assert all(s.key.startswith("case:FCRM-") for s in sources)


async def test_jurisdiction_list_yields_one_list_source_with_every_listed_line() -> (
    None
):
    sources = await _sources(
        "jurisdiction_list", JurisdictionListInput(version="latest")
    )
    assert len(sources) == 1
    ref = sources[0].source_ref
    assert isinstance(ref, ListSourceRef)
    assert ref.list_id == FATF_LIST_ID
    assert "Iran" in sources[0].text


async def test_control_inventory_yields_a_source_per_control_at_the_asked_version() -> (
    None
):
    request = ControlInventoryInput(domain="channel", controls_v=VERSIONS.controls_v)
    sources = await _sources("control_inventory", request)
    assert sources
    for source in sources:
        assert isinstance(source.source_ref, ControlSourceRef)
        assert source.source_ref.controls_v == VERSIONS.controls_v


async def test_audit_log_yields_the_cases_own_records() -> None:
    sources = await _sources("audit_log", AuditLogInput(case_id=DEMO_CASE_ID))
    assert sources
    assert all(isinstance(s.source_ref, RecordSourceRef) for s in sources)
    assert {s.source_ref.case_id for s in sources} == {DEMO_CASE_ID}  # type: ignore[union-attr]


async def test_case_record_yields_the_case_field_lines() -> None:
    request = CaseRecordInput(case_id=DEMO_CASE_ID, submission_version=1)
    sources = await _sources("case_record", request)
    assert [s.key for s in sources] == [f"case:{DEMO_CASE_ID}"]
    assert "F0" in sources[0].text


async def test_a_tool_whose_result_is_not_citable_yields_nothing() -> None:
    request = FieldRulesInput(change_types=["vendor_onboarding"], fieldset_v=6)
    assert await _sources("field_rules", request) == []
