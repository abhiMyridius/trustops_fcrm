"""``build_http_registry`` and ``ToolClients``: which tools exist, that the rest
fail loudly instead of falling back to the seed, that there is no scoring tool,
and how the peer clients are built and closed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fcrm_agents.spec import REGISTRY as AGENT_SPECS
from fcrm_common.objectstore import ObjectStore
from fcrm_contracts.enums import AgentName
from fcrm_contracts.tools.rules_check import RulesCheckInput
from fcrm_prompts import PromptLibrary
from fcrm_tools import (
    TOOL_NAMES,
    InMemoryTTLCache,
    ToolClients,
    ToolNotWiredError,
    UnknownToolError,
    build_http_registry,
)
from fcrm_tools.http_backed import WIRED_TOOLS, HttpToolRegistry

REPO_AI = Path(__file__).parents[4] / "ai"


@pytest.fixture
def registry(tool_clients: ToolClients, object_store: ObjectStore) -> Any:
    """The HTTP registry over the scripted peers."""
    return build_http_registry(tool_clients, object_store)


def test_the_registry_holds_all_thirteen_tools_and_nothing_else(
    registry: Any,
) -> None:
    assert isinstance(registry, HttpToolRegistry)
    assert registry.names() == WIRED_TOOLS
    assert set(registry.names()) == set(TOOL_NAMES)
    assert len(registry) == 13


def test_there_is_no_scoring_engine_tool_anywhere(registry: Any) -> None:
    """Agents never score (ADR-07): no fourteenth tool, not registered, not
    named, and asking for one is a plain unknown tool."""
    assert "scoring_engine" not in TOOL_NAMES
    assert "scoring_engine" not in WIRED_TOOLS
    assert "scoring_engine" not in registry
    with pytest.raises(UnknownToolError) as raised:
        registry.get("scoring_engine")
    assert not isinstance(raised.value, ToolNotWiredError)


def test_a_partial_registry_says_why_a_known_tool_is_missing(registry: Any) -> None:
    partial = HttpToolRegistry([registry.get("case_record")])
    with pytest.raises(ToolNotWiredError) as raised:
        partial.get("policy_search")
    assert "policy_search" in str(raised.value)
    assert "case_record" in str(raised.value)  # says what is registered
    with pytest.raises(UnknownToolError):  # still an unknown tool to old callers
        partial.get("policy_search")
    with pytest.raises(ToolNotWiredError):
        partial.subset(["case_record", "policy_search"])


def test_every_tool_has_the_bare_name_as_its_permit_key(registry: Any) -> None:
    assert registry.permit_keys() == {name: name for name in TOOL_NAMES}


@pytest.mark.parametrize("agent", list(AgentName))
def test_every_agent_has_every_tool_it_declares_and_its_pack_builder_reads(
    registry: Any, agent: AgentName
) -> None:
    """The whole pipeline runs on this registry: everything each of the nine
    agents declares, to the model and to its pack builder, is registered."""
    spec = AGENT_SPECS[agent]
    for name in (*spec.tools, *spec.pack_tools):
        assert name in registry, f"{agent.value} needs {name}"


def test_the_registry_is_exactly_what_the_agent_yaml_specs_declare(
    registry: Any,
) -> None:
    """Lock: every tool an agent may call is present and there is no extra one.
    ``ai/agents/*.agent.yaml`` is the declaration; the registry must equal the
    union of every agent's ``tools`` and ``pack_tools``."""
    library = PromptLibrary(REPO_AI)
    declared = {
        name: [*spec.tools, *spec.pack_tools]
        for name, spec in library.agent_specs.items()
    }
    assert len(declared) == 9
    report = registry.check_declared(declared)
    assert report.missing == {}
    assert report.undeclared == []
    union = {tool for tools in declared.values() for tool in tools}
    assert set(registry.names()) == union
    assert "scoring_engine" not in union


def test_the_agent_spec_check_over_the_real_ai_root_passes(registry: Any) -> None:
    report = registry.check_agent_specs(REPO_AI)
    assert report.skipped is False
    assert report.ok is True
    assert report.missing == {}


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
