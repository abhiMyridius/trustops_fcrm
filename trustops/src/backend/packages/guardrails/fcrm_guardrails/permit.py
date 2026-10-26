"""Guard 2: PermitGuard (pre; H2; design B §4.3).

May this agent call this tool? Two independent conditions, both required:

1. The tool is in the agent's own allow-list (``AgentSpec.tools``, the P7
   agent->tools table), when the caller supplies it. This is the Python
   mirror design A §8 describes; it is an extra *refusal*, never a way to
   allow a call OPA did not allow.
2. OPA explicitly allows it (:class:`~fcrm_guardrails.opa_client.OPAClient`).

Fail closed, always: an OPA error, a timeout, or any return value other than
``True`` is a denial. The broad ``except`` in :meth:`PermitGuard.decide` is
the single deliberate exception to "catch specific exceptions": the client
is injected and may raise anything, and the only safe reading of "the
permit check did not complete" is "not permitted". The exception is logged
with its traceback; nothing is swallowed.

Used twice by H2: once at job start over the agent's whole allow-list (any
denial blocks the job; the summary is the walkthrough's "intake agent
permitted: document_reader, ..."), and per tool call through
:meth:`PermitGuard.decide` (a denial becomes a tool error the model sees).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Collection, Mapping
from typing import TYPE_CHECKING, Optional

from pydantic import BaseModel, ConfigDict

from fcrm_guardrails.base import Guard, GuardName, GuardResult, GuardStatus
from fcrm_guardrails.opa_client import OPAClient

if TYPE_CHECKING:
    from fcrm_guardrails.context import GuardContext

logger = logging.getLogger(__name__)

DEFAULT_PERMIT_TIMEOUT_S = 6.0
"""The whole decision's deadline, retries included (D-130). It is longer than
the OPA client's own 2 s HTTP timeout, so the client's transport retry can run
inside it, and a decision OPA already answered is still read after the event
loop stalls for a moment."""
PERMIT_ATTEMPTS = 2
"""A permit is asked at most twice: once, then once more on a retryable error."""
PERMIT_RETRY_PAUSE_S = 0.2
MIN_RETRY_WINDOW_S = 0.5
"""A retry is only worth starting with at least this long left before the
deadline."""


class PermitDecision(BaseModel):
    """One tool's permit outcome."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    tool: str
    allowed: bool
    reason: str


class PermitGuard(Guard):
    """Guard 2: OPA agent-to-tool permits, deny-closed."""

    name = GuardName.PERMIT
    stage = "pre"

    def __init__(
        self,
        opa: OPAClient,
        *,
        allow_list: Optional[Mapping[str, Collection[str]]] = None,
        timeout_s: float = DEFAULT_PERMIT_TIMEOUT_S,
        retry_on: tuple[type[BaseException], ...] = (TimeoutError,),
    ) -> None:
        """Check permits with ``opa``.

        Args:
            opa: The OPA client.
            allow_list: Agent -> the tools it may call at all.
            timeout_s: The deadline of one decision, its retry included.
            retry_on: The errors that mean OPA could not be asked, so the
                decision is asked once more (a timeout; the caller adds its
                client's "unavailable" error). A deny is never retried, and
                every other error denies at once.
        """
        self._opa = opa
        self._allow_list = allow_list
        self._timeout_s = timeout_s
        self._retry_on = retry_on

    async def decide(
        self, *, agent: str, tool: str, case_id: str, role: str, run_id: str
    ) -> PermitDecision:
        """Permit decision for one tool call; never raises, never allows by
        default."""
        if self._allow_list is not None and tool not in self._allow_list.get(agent, ()):
            return PermitDecision(
                tool=tool, allowed=False, reason="not in the agent's tool list"
            )
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_s
        attempt = 0
        while True:
            attempt += 1
            try:
                verdict = await asyncio.wait_for(
                    self._opa.tool_permit(
                        agent=agent,
                        tool=tool,
                        case_id=case_id,
                        role=role,
                        run_id=run_id,
                    ),
                    timeout=max(deadline - loop.time(), 0.0),
                )
            except self._retry_on as exc:
                left = deadline - loop.time()
                extra = {"agent": agent, "tool": tool, "case_id": case_id}
                if attempt < PERMIT_ATTEMPTS and left > MIN_RETRY_WINDOW_S:
                    logger.warning(
                        "opa permit not answered (%s); asking once more",
                        type(exc).__name__,
                        extra=extra,
                    )
                    await asyncio.sleep(PERMIT_RETRY_PAUSE_S)
                    continue
                if isinstance(exc, TimeoutError):
                    logger.warning("opa permit timed out; denying", extra=extra)
                    return PermitDecision(
                        tool=tool, allowed=False, reason="opa timeout"
                    )
                logger.warning(
                    "opa permit unavailable; denying",
                    extra={**extra, "error": type(exc).__name__},
                )
                return PermitDecision(
                    tool=tool,
                    allowed=False,
                    reason=f"opa unavailable: {type(exc).__name__}",
                )
            except Exception as exc:  # deny-closed on any client failure (module doc)
                logger.exception(
                    "opa permit failed; denying",
                    extra={"agent": agent, "tool": tool, "case_id": case_id},
                )
                return PermitDecision(
                    tool=tool, allowed=False, reason=f"opa error: {type(exc).__name__}"
                )
            break
        if verdict is True:
            return PermitDecision(tool=tool, allowed=True, reason="opa allow")
        return PermitDecision(tool=tool, allowed=False, reason="opa deny")

    async def check(self, ctx: GuardContext) -> GuardResult:
        """Job-start check over every tool in ``ctx.tools``."""
        started = time.perf_counter()
        if ctx.agent is None:
            return self._result(
                GuardStatus.BLOCKED,
                "no agent in context; failing closed",
                started=started,
            )
        agent = ctx.agent.value
        decisions = [
            await self.decide(
                agent=agent,
                tool=tool,
                case_id=ctx.case_id,
                role=ctx.role,
                run_id=ctx.run_id,
            )
            for tool in ctx.tools
        ]
        allowed = [d.tool for d in decisions if d.allowed]
        denied = {d.tool: d.reason for d in decisions if not d.allowed}
        details: dict[str, object] = {
            "agent": agent,
            "allowed": allowed,
            "denied": denied,
        }
        if denied:
            listed = ", ".join(f"{tool} ({why})" for tool, why in denied.items())
            return self._result(
                GuardStatus.BLOCKED,
                f"{agent} agent denied: {listed}",
                started=started,
                details=details,
            )
        tools = ", ".join(allowed) if allowed else "no tools"
        return self._result(
            GuardStatus.PASSED,
            f"{agent} agent permitted: {tools}",
            started=started,
            details=details,
        )
