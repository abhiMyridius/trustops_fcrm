"""Guard 2: OPA tool permits, deny-closed."""

from __future__ import annotations

import asyncio

from fcrm_contracts.enums import AgentName
from fcrm_guardrails.base import GuardStatus
from fcrm_guardrails.context import GuardContext
from fcrm_guardrails.opa_client import OPAClient
from fcrm_guardrails.permit import PermitGuard

INTAKE_TOOLS = [
    "document_reader",
    "precedent_search",
    "jurisdiction_list",
    "field_rules",
]
ALLOW_LIST = {"intake": INTAKE_TOOLS, "research": ["policy_search"]}


class FakeOpa:
    """Structural stand-in for fcrm_common's OPA client (local to this module)."""

    def __init__(self, allowed: set[str], *, fail: bool = False, slow: bool = False):
        self.allowed = allowed
        self.fail = fail
        self.slow = slow
        self.calls: list[dict[str, str]] = []

    async def tool_permit(
        self, *, agent: str, tool: str, case_id: str, role: str, run_id: str
    ) -> bool:
        self.calls.append(
            {"agent": agent, "tool": tool, "role": role, "run_id": run_id}
        )
        if self.fail:
            raise ConnectionError("opa unreachable")
        if self.slow:
            await asyncio.sleep(1)
        return tool in self.allowed


class TruthyOpa:
    async def tool_permit(
        self, *, agent: str, tool: str, case_id: str, role: str, run_id: str
    ) -> bool:
        return "yes"  # type: ignore[return-value]  # a non-bool must not allow


def _ctx(agent: AgentName, tools: list[str]) -> GuardContext:
    return GuardContext(
        case_id="FCRM-2026-0151", run_id="run_1", agent=agent, tools=tools
    )


def test_fake_satisfies_the_protocol() -> None:
    assert isinstance(FakeOpa(set()), OPAClient)


async def test_check_all_permitted_matches_walkthrough_summary() -> None:
    opa = FakeOpa(set(INTAKE_TOOLS))
    result = await PermitGuard(opa, allow_list=ALLOW_LIST).check(
        _ctx(AgentName.INTAKE, INTAKE_TOOLS)
    )
    assert result.status is GuardStatus.PASSED
    assert result.summary == (
        "intake agent permitted: document_reader, precedent_search, "
        "jurisdiction_list, field_rules"
    )
    assert opa.calls[0] == {
        "agent": "intake",
        "tool": "document_reader",
        "role": "agent",
        "run_id": "run_1",
    }


async def test_check_opa_deny_blocks() -> None:
    result = await PermitGuard(FakeOpa({"policy_search"})).check(
        _ctx(AgentName.RESEARCH, ["policy_search", "scoring_engine"])
    )
    assert result.status is GuardStatus.BLOCKED
    assert result.details["denied"] == {"scoring_engine": "opa deny"}


async def test_decide_tool_outside_allow_list_is_refused_without_asking_opa() -> None:
    opa = FakeOpa({"scoring_engine"})
    decision = await PermitGuard(opa, allow_list=ALLOW_LIST).decide(
        agent="research", tool="scoring_engine", case_id="c", role="agent", run_id="r"
    )
    assert not decision.allowed
    assert opa.calls == []


async def test_decide_opa_error_denies() -> None:
    decision = await PermitGuard(FakeOpa(set(), fail=True)).decide(
        agent="intake", tool="field_rules", case_id="c", role="agent", run_id="r"
    )
    assert not decision.allowed
    assert decision.reason == "opa error: ConnectionError"


async def test_decide_opa_timeout_denies() -> None:
    guard = PermitGuard(FakeOpa({"field_rules"}, slow=True), timeout_s=0.01)
    decision = await guard.decide(
        agent="intake", tool="field_rules", case_id="c", role="agent", run_id="r"
    )
    assert not decision.allowed
    assert decision.reason == "opa timeout"


async def test_decide_non_bool_verdict_denies() -> None:
    decision = await PermitGuard(TruthyOpa()).decide(
        agent="intake", tool="field_rules", case_id="c", role="agent", run_id="r"
    )
    assert not decision.allowed


async def test_check_without_agent_fails_closed() -> None:
    result = await PermitGuard(FakeOpa(set())).check(
        GuardContext(case_id="c", run_id="r", tools=["field_rules"])
    )
    assert result.status is GuardStatus.BLOCKED
