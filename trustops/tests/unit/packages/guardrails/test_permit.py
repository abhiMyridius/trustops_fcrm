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


class Unavailable(Exception):
    """Stands in for fcrm_common's ``AuthzUnavailableError`` (D-130)."""


class ScriptedOpa:
    """Answers each call from ``script``: ``"slow"`` sleeps past the deadline of
    one attempt, an exception class is raised, a bool is the verdict."""

    def __init__(self, *script: object, delay: float = 1.0) -> None:
        self.script = list(script)
        self.delay = delay
        self.calls = 0

    async def tool_permit(
        self, *, agent: str, tool: str, case_id: str, role: str, run_id: str
    ) -> bool:
        self.calls += 1
        step = self.script.pop(0)
        if step == "slow":
            await asyncio.sleep(self.delay)
            return True
        if isinstance(step, type) and issubclass(step, BaseException):
            raise step("opa")
        return bool(step)


def _decide(guard: PermitGuard) -> object:
    return guard.decide(
        agent="intake", tool="field_rules", case_id="c", role="agent", run_id="r"
    )


async def test_a_slow_answer_inside_the_deadline_is_still_read() -> None:
    """D-130: OPA answered, the event loop stalled for a moment; the decision
    waits out the stall instead of denying."""
    opa = ScriptedOpa("slow", delay=0.05)
    decision = await _decide(PermitGuard(opa, timeout_s=1.0))
    assert decision.allowed  # type: ignore[attr-defined]
    assert opa.calls == 1


async def test_an_unavailable_opa_is_asked_once_more() -> None:
    opa = ScriptedOpa(Unavailable, True)
    decision = await _decide(PermitGuard(opa, retry_on=(TimeoutError, Unavailable)))
    assert decision.allowed  # type: ignore[attr-defined]
    assert opa.calls == 2


async def test_a_second_failure_denies() -> None:
    opa = ScriptedOpa(Unavailable, Unavailable, True)
    decision = await _decide(PermitGuard(opa, retry_on=(TimeoutError, Unavailable)))
    assert not decision.allowed  # type: ignore[attr-defined]
    assert decision.reason == "opa unavailable: Unavailable"  # type: ignore[attr-defined]
    assert opa.calls == 2


async def test_a_deny_is_never_retried() -> None:
    opa = ScriptedOpa(False, True)
    decision = await _decide(PermitGuard(opa, retry_on=(TimeoutError, Unavailable)))
    assert not decision.allowed  # type: ignore[attr-defined]
    assert decision.reason == "opa deny"  # type: ignore[attr-defined]
    assert opa.calls == 1


async def test_an_error_not_named_retryable_denies_at_once() -> None:
    opa = ScriptedOpa(Unavailable, True)
    decision = await _decide(PermitGuard(opa))  # timeouts only
    assert not decision.allowed  # type: ignore[attr-defined]
    assert opa.calls == 1


async def test_the_retry_stays_inside_the_deadline() -> None:
    opa = ScriptedOpa("slow", "slow", delay=5.0)
    started = asyncio.get_running_loop().time()
    decision = await _decide(PermitGuard(opa, timeout_s=0.3))
    assert not decision.allowed  # type: ignore[attr-defined]
    assert decision.reason == "opa timeout"  # type: ignore[attr-defined]
    assert asyncio.get_running_loop().time() - started < 1.0
