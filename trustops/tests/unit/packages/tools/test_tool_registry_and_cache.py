"""ToolRegistry, the agent-spec check, and the retrieval TTL cache."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fcrm_contracts.api.policy import SearchRequest
from fcrm_contracts.tools.case_record import CaseRecordInput
from fcrm_contracts.tools.jurisdiction_list import JurisdictionListInput
from fcrm_tools import (
    CachedTool,
    InMemoryTTLCache,
    ToolRegistry,
    UnknownToolError,
    build_seed_registry,
    cache_key,
    permit_key_for,
    permit_pair,
    with_retrieval_cache,
)
from fcrm_tools.seed_backed import JurisdictionListTool, RulesCheckTool

FIXTURE_AI = Path(__file__).parents[1] / "prompts_loader" / "fixtures" / "ai"
REPO_AI = Path(__file__).parents[4] / "ai"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class CountingTool:
    """Wraps a real tool and counts calls through to it."""

    def __init__(self) -> None:
        self.inner = JurisdictionListTool()
        self.calls = 0
        self.name = self.inner.name
        self.permit_key = self.inner.permit_key
        self.input_model = self.inner.input_model
        self.output_model = self.inner.output_model

    async def call(self, input: JurisdictionListInput) -> object:  # noqa: A002
        self.calls += 1
        return await self.inner.call(input)


def test_registry_duplicate_and_unknown() -> None:
    registry = ToolRegistry([RulesCheckTool()])
    with pytest.raises(ValueError, match="already registered"):
        registry.register(RulesCheckTool())
    with pytest.raises(UnknownToolError):
        registry.get("scoring_engine")
    assert "rules_check" in registry
    assert len(registry) == 1


def test_registry_subset_and_permit_keys() -> None:
    registry = build_seed_registry()
    subset = registry.subset(["policy_search", "audit_log"])
    assert subset.names() == ("policy_search", "audit_log")
    assert subset.permit_keys() == {
        "policy_search": "policy_search",
        "audit_log": "audit_log",
    }


def test_permit_key_convention_is_bare_tool_name() -> None:
    assert permit_key_for("policy_search") == "policy_search"
    assert permit_pair("research", "policy_search") == "research:policy_search"


def test_check_declared_reports_missing_and_undeclared() -> None:
    registry = build_seed_registry()
    report = registry.check_declared(
        {"assessment": ["control_inventory", "evidence_store", "scoring_engine"]}
    )
    assert report.ok is False
    assert report.missing == {"assessment": ["scoring_engine"]}
    assert "audit_log" in report.undeclared


def test_check_agent_specs_skips_when_ai_root_absent(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING):
        report = build_seed_registry().check_agent_specs(tmp_path / "ai")
    assert report.skipped is True
    assert report.ok is True
    assert "check skipped" in caplog.text


def test_check_agent_specs_reads_agent_yaml() -> None:
    report = build_seed_registry().check_agent_specs(FIXTURE_AI)
    assert report.skipped is False
    assert report.agents_checked == ["research", "screening"]
    assert report.ok is True


@pytest.mark.skipif(not (REPO_AI / "agents").is_dir(), reason="ai/ lane absent")
def test_check_agent_specs_real_ai_every_declared_tool_is_registered() -> None:
    report = build_seed_registry().check_agent_specs(REPO_AI)
    assert report.skipped is False
    assert len(report.agents_checked) == 9
    assert report.missing == {}
    assert report.ok is True


def test_cache_key_ignores_field_order_but_not_values() -> None:
    a = SearchRequest(index="policy_clauses", query="agent", top_k=3)
    b = SearchRequest.model_validate(
        {"top_k": 3, "query": "agent", "index": "policy_clauses"}
    )
    c = SearchRequest(index="policy_clauses", query="agents", top_k=3)
    assert cache_key("policy_search", a) == cache_key("policy_search", b)
    assert cache_key("policy_search", a) != cache_key("policy_search", c)
    assert cache_key("policy_search", a)[0] == "policy_search"


def test_in_memory_cache_ttl_expiry() -> None:
    clock = FakeClock()
    cache = InMemoryTTLCache(clock=clock)
    key = ("case_record", "h")
    value = CaseRecordInput(case_id="C1")
    cache.set(key, value, ttl_s=10)
    clock.now = 9.9
    assert cache.get(key) == value
    clock.now = 10.0
    assert cache.get(key) is None
    assert (cache.hits, cache.misses) == (1, 1)


def test_in_memory_cache_no_ttl_never_expires_and_copies() -> None:
    clock = FakeClock()
    cache = InMemoryTTLCache(clock=clock)
    key = ("document_reader", "h")
    cache.set(key, CaseRecordInput(case_id="C1"), ttl_s=None)
    clock.now = 1e9
    got = cache.get(key)
    assert isinstance(got, CaseRecordInput)
    got.case_id = "mutated"
    again = cache.get(key)
    assert isinstance(again, CaseRecordInput)
    assert again.case_id == "C1"


def test_in_memory_cache_evicts_oldest() -> None:
    cache = InMemoryTTLCache(max_entries=2)
    for n in range(3):
        cache.set(("t", str(n)), CaseRecordInput(case_id=str(n)), ttl_s=None)
    assert len(cache) == 2
    assert cache.get(("t", "0")) is None


async def test_cached_tool_serves_repeat_calls_from_cache() -> None:
    counting = CountingTool()
    tool = CachedTool(counting, InMemoryTTLCache(), ttl_s=60)
    request = JurisdictionListInput(version="latest")
    first = await tool.call(request)
    second = await tool.call(request)
    await tool.call(JurisdictionListInput(version="fixture"))
    assert first == second
    assert counting.calls == 2
    assert tool.permit_key == "jurisdiction_list"


def test_with_retrieval_cache_wraps_only_retrieval_tools() -> None:
    wrapped = with_retrieval_cache(build_seed_registry(), InMemoryTTLCache())
    assert isinstance(wrapped.get("policy_search"), CachedTool)
    assert isinstance(wrapped.get("document_reader"), CachedTool)
    assert not isinstance(wrapped.get("case_record"), CachedTool)
    assert not isinstance(wrapped.get("audit_log"), CachedTool)
