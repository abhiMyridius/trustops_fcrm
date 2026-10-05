"""``build_http_registry`` and ``ToolClients``: which tools exist, that the rest
fail loudly instead of falling back to the seed, that there is no scoring tool,
and how the peer clients are built and closed."""

from __future__ import annotations

from typing import Any

import pytest
from fcrm_agents.spec import REGISTRY as AGENT_SPECS
from fcrm_common.objectstore import ObjectStore
from fcrm_contracts.enums import AgentName
from fcrm_contracts.tools.rules_check import RulesCheckInput
from fcrm_tools import (
    TOOL_NAMES,
    InMemoryTTLCache,
    ToolClients,
    ToolNotWiredError,
    UnknownToolError,
    build_http_registry,
)
from fcrm_tools.http_backed import NOT_WIRED_TOOLS, WIRED_TOOLS, HttpToolRegistry


@pytest.fixture
def registry(tool_clients: ToolClients, object_store: ObjectStore) -> Any:
    """The HTTP registry over the scripted peers."""
    return build_http_registry(tool_clients, object_store)


def test_the_registry_holds_exactly_the_tools_that_have_a_backing(
    registry: Any,
) -> None:
    assert isinstance(registry, HttpToolRegistry)
    assert registry.names() == WIRED_TOOLS
    assert set(WIRED_TOOLS) == {
        "document_reader",
        "case_record",
        "field_rules",
        "jurisdiction_list",
        "precedent_search",
        "rules_check",
    }


def test_wired_and_not_wired_partition_the_thirteen_tools() -> None:
    assert set(WIRED_TOOLS) | set(NOT_WIRED_TOOLS) == set(TOOL_NAMES)
    assert not set(WIRED_TOOLS) & set(NOT_WIRED_TOOLS)
    assert set(NOT_WIRED_TOOLS) == {
        "policy_search",
        "control_inventory",
        "evidence_store",
        "evidence_ledger",
        "audit_log",
        "submission_diff",
        "case_search",
    }


@pytest.mark.parametrize("name", NOT_WIRED_TOOLS)
def test_an_unwired_tool_is_a_clear_error_never_a_seed_fallback(
    registry: Any, name: str
) -> None:
    with pytest.raises(ToolNotWiredError) as raised:
        registry.get(name)
    assert name in str(raised.value)
    assert "no HTTP backing yet" in str(raised.value)
    assert "document_reader" in str(raised.value)  # says what is wired
    assert name not in registry


def test_an_unwired_tool_is_still_an_unknown_tool_to_existing_callers(
    registry: Any,
) -> None:
    with pytest.raises(UnknownToolError):
        registry.get("policy_search")


def test_there_is_no_scoring_engine_tool_anywhere() -> None:
    """Agents never score (ADR-07): no thirteenth-plus tool, not wired, not
    named in the registry, and asking for one is a plain unknown tool."""
    assert "scoring_engine" not in TOOL_NAMES
    assert "scoring_engine" not in WIRED_TOOLS


def test_a_name_that_is_not_a_tool_is_a_plain_unknown_tool(registry: Any) -> None:
    with pytest.raises(UnknownToolError) as raised:
        registry.get("scoring_engine")
    assert not isinstance(raised.value, ToolNotWiredError)


def test_a_subset_with_an_unwired_tool_is_refused(registry: Any) -> None:
    assert registry.subset(["case_record", "field_rules"]).names() == (
        "case_record",
        "field_rules",
    )
    with pytest.raises(ToolNotWiredError):
        registry.subset(["case_record", "policy_search"])


def test_every_tool_has_the_bare_name_as_its_permit_key(registry: Any) -> None:
    assert registry.permit_keys() == {name: name for name in WIRED_TOOLS}


@pytest.mark.parametrize("agent", [AgentName.INTAKE, AgentName.SCREENING])
def test_the_intake_and_screening_agents_have_every_tool_they_need(
    registry: Any, agent: AgentName
) -> None:
    """The V1 vertical runs on this registry: everything intake and screening
    declare, to the model and to their pack builder, is wired."""
    spec = AGENT_SPECS[agent]
    for name in (*spec.tools, *spec.pack_tools):
        assert name in registry, f"{agent.value} needs {name}"


def test_the_agents_that_need_an_unwired_tool_are_named_by_the_spec_check(
    registry: Any,
) -> None:
    declared = {
        agent.value: [*spec.tools, *spec.pack_tools]
        for agent, spec in AGENT_SPECS.items()
    }
    report = registry.check_declared(declared)
    assert "intake" not in report.missing
    assert "screening" not in report.missing
    assert set(report.missing["research"]) == {
        "policy_search",
        "control_inventory",
        "submission_diff",
    }
    missing_anywhere = {tool for tools in report.missing.values() for tool in tools}
    assert missing_anywhere <= set(NOT_WIRED_TOOLS)


async def test_rules_check_runs_in_process_and_calls_no_peer(
    registry: Any, peers: Any
) -> None:
    out = await registry.get("rules_check").call(
        RulesCheckInput(rule_ids=["R-6"], subject={})
    )
    assert out.results[0].rule_id == "R-6"
    assert peers.requests == []


def test_a_registry_without_a_cache_gets_its_own(
    tool_clients: ToolClients, object_store: ObjectStore
) -> None:
    first = build_http_registry(tool_clients, object_store)
    second = build_http_registry(tool_clients, object_store, InMemoryTTLCache())
    assert first.names() == second.names()
    assert first.get("field_rules") is not second.get("field_rules")


# ── ToolClients ────────────────────────────────────────────────────────────


async def test_create_builds_one_client_per_peer_with_the_timeouts_and_token(
    peers: Any,
) -> None:
    import httpx

    clients = ToolClients.create(
        case_url="http://case.test/api/fcrm",
        policy_url="http://policy.test/api/fcrm/policy",
        audit_url="http://audit.test/api/fcrm/audit",
        caller="fcrm-agent-worker",
        token_secret="s" * 32,
        transport=httpx.MockTransport(peers.handle),
    )
    try:
        assert [
            c.dependency for c in (clients.case, clients.policy, clients.audit)
        ] == [
            "case",
            "policy",
            "audit",
        ]
        peers.respond("policy.test", "GET", "/api/fcrm/policy/ping", {"ok": True})
        response = await clients.policy.get("/ping")
        assert response.json() == {"ok": True}
        sent = peers.requests[-1]
        assert sent.headers["authorization"].startswith("Bearer ")
        timeout = sent.extensions["timeout"]
        assert timeout["connect"] == 2.0
        assert timeout["read"] == 10.0
    finally:
        await clients.aclose()


async def test_create_can_share_an_existing_audit_client(peers: Any) -> None:
    import httpx
    from fcrm_common.http import ServiceClient

    audit = ServiceClient(
        "http://audit.test",
        dependency="audit",
        transport=httpx.MockTransport(peers.handle),
    )
    clients = ToolClients.create(
        case_url="http://case.test",
        policy_url="http://policy.test",
        audit_url="http://ignored.test",
        caller="fcrm-agent-worker",
        audit_client=audit,
    )
    assert clients.audit is audit
    await clients.aclose()
    await clients.aclose()  # closing twice is harmless


async def test_aclose_closes_every_client_even_when_one_fails(
    tool_clients: ToolClients,
) -> None:
    closed: list[str] = []

    async def failing() -> None:
        closed.append("case")
        raise RuntimeError("boom")

    async def closing_policy() -> None:
        closed.append("policy")

    async def closing_audit() -> None:
        closed.append("audit")

    tool_clients.case.aclose = failing  # type: ignore[method-assign]
    tool_clients.policy.aclose = closing_policy  # type: ignore[method-assign]
    tool_clients.audit.aclose = closing_audit  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="boom"):
        await tool_clients.aclose()
    assert sorted(closed) == ["audit", "case", "policy"]
