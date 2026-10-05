"""The common guard interface (design B §4.1).

Every guard is a :class:`Guard` with a fixed ``name`` and ``stage`` and one
coroutine, :meth:`Guard.check`, that takes a
:class:`~fcrm_guardrails.context.GuardContext` and returns a
:class:`GuardResult`. Guards never raise for a *failed check*: a failure is
a ``GuardResult`` with status ``blocked`` or ``retry``. They raise only for
a programming error (a missing required input is a ``blocked`` result,
never a silent pass: every guard fails closed).

The seven guards run in the fixed order of :data:`GUARD_ORDER`
(``ai/guards/guards.yaml``; BUILD-PLAN §3.4). :class:`GuardChain` runs a
configured subset in that order and stops at the first ``blocked`` or
``retry``.

Each ``GuardResult`` is written twice by the caller (design B §4.1): to the
evidence ledger (``ledger_items``, one per checked item) and to the audit
chain through S4 with ``type=audit_type``. ``model_dump()`` is safe to
write: transformed payloads (masked text) are excluded from serialisation.

The context contract other lanes (agent-worker H2, caseflow H1) satisfy
lives in :mod:`fcrm_guardrails.context` to avoid an import cycle with the
budget ledger it carries.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Collection, Sequence
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar, Final, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from fcrm_guardrails.context import GuardContext

logger = logging.getLogger(__name__)

GuardStage = Literal["pre", "post", "state"]

_PRE_STAGES: Final[tuple[GuardStage, ...]] = ("pre",)
_POST_STAGES: Final[tuple[GuardStage, ...]] = ("post", "state")


class GuardName(StrEnum):
    """The seven guards, in their fixed order."""

    INPUT_SCAN = "input_scan"
    PERMIT = "permit"
    BUDGET = "budget"
    OUTPUT_SHAPE = "output_shape"
    RULES = "rules"
    CRITIC = "critic"
    HUMAN_GATE = "human_gate"


GUARD_ORDER: Final[tuple[GuardName, ...]] = tuple(GuardName)
"""``ai/guards/guards.yaml: order`` (design B §1.7)."""

AUDIT_TYPES: Final[dict[GuardName, str]] = {
    GuardName.INPUT_SCAN: "guard.input",
    GuardName.PERMIT: "guard.permit",
    GuardName.BUDGET: "guard.budget",
    GuardName.OUTPUT_SHAPE: "guard.output",
    GuardName.RULES: "guard.rules",
    GuardName.CRITIC: "critic.findings",
    GuardName.HUMAN_GATE: "gate.decided",
}
"""Audit record ``type`` per guard (design B §1.7 ``writes``; the P7 audit
vocabulary in design A §2.4.1)."""


class GuardStatus(StrEnum):
    """Design B §4.1. ``passed``/``masked``/``annotated`` let the call
    proceed; ``retry`` asks for one more attempt on the fallback model;
    ``blocked`` stops it."""

    PASSED = "passed"
    MASKED = "masked"
    ANNOTATED = "annotated"
    RETRY = "retry"
    BLOCKED = "blocked"


PROCEED_STATUSES: Final[frozenset[GuardStatus]] = frozenset(
    {GuardStatus.PASSED, GuardStatus.MASKED, GuardStatus.ANNOTATED}
)


class LedgerAnnotation(BaseModel):
    """One per-item check result written next to a proposal in the ledger
    (design B §4.5: ``LedgerAnnotation{item_id, check, ok}``)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    check: str
    ok: bool
    detail: Optional[str] = None


class GuardResult(BaseModel):
    """What every guard returns (design B §4.1).

    ``summary`` is the one-line human text that becomes the audit record's
    ``what`` (e.g. "15 of 15 sourced · 0 rejected"); ``details`` is the
    structured payload. ``transformed`` carries non-audited outputs a caller
    needs to continue (Guard 1's masked text) and is excluded from
    ``model_dump()`` so it can never leak into an audit write by accident.
    """

    model_config = ConfigDict(extra="forbid")

    guard: GuardName
    status: GuardStatus
    summary: str
    details: dict[str, Any] = Field(default_factory=dict)
    ledger_items: list[LedgerAnnotation] = Field(default_factory=list)
    audit_type: str
    duration_ms: int = 0
    transformed: dict[str, str] = Field(default_factory=dict, exclude=True)

    @property
    def passed(self) -> bool:
        """True when the guarded step may proceed."""
        return self.status in PROCEED_STATUSES

    @property
    def reason(self) -> Optional[str]:
        """The summary when the step may not proceed, else ``None``."""
        return None if self.passed else self.summary


class Guard(ABC):
    """Base class for the seven guards. Subclasses set ``name`` and ``stage``
    and implement :meth:`check`."""

    name: ClassVar[GuardName]
    stage: ClassVar[GuardStage]

    @abstractmethod
    async def check(self, ctx: GuardContext) -> GuardResult:
        """Run this guard over ``ctx``; never raise for a failed check."""

    def _result(
        self,
        status: GuardStatus,
        summary: str,
        *,
        started: float,
        details: Optional[dict[str, Any]] = None,
        ledger_items: Optional[list[LedgerAnnotation]] = None,
        transformed: Optional[dict[str, str]] = None,
    ) -> GuardResult:
        """Build this guard's result with ``duration_ms`` measured from
        ``started`` (a ``time.perf_counter()`` reading)."""
        return GuardResult(
            guard=self.name,
            status=status,
            summary=summary,
            details=details or {},
            ledger_items=ledger_items or [],
            audit_type=AUDIT_TYPES[self.name],
            duration_ms=int((time.perf_counter() - started) * 1000),
            transformed=transformed or {},
        )


class GuardChain:
    """Runs guards in :data:`GUARD_ORDER`, stopping at the first result that
    may not proceed (design B §4.1 ``GuardChain``).

    Raises:
        ValueError: at construction when the guards are not in the fixed
            order or a guard appears twice, so a misconfigured chain fails at
            startup rather than running guards in the wrong order.
    """

    def __init__(self, guards: Sequence[Guard]) -> None:
        """Hold ``guards`` after checking their order."""
        positions = [GUARD_ORDER.index(g.name) for g in guards]
        if positions != sorted(set(positions)):
            names = [g.name.value for g in guards]
            msg = f"guards must run once each in {list(GUARD_ORDER)}; got {names}"
            raise ValueError(msg)
        self._guards = tuple(guards)

    async def run(
        self, ctx: GuardContext, *, stages: Optional[Collection[GuardStage]] = None
    ) -> list[GuardResult]:
        """Run the chain (optionally only guards in ``stages``); the last
        result is the one that stopped it, if any did."""
        results: list[GuardResult] = []
        for guard in self._guards:
            if stages is not None and guard.stage not in stages:
                continue
            result = await guard.check(ctx)
            results.append(result)
            if not result.passed:
                logger.info(
                    "guard chain stopped",
                    extra={"guard": guard.name.value, "status": result.status.value},
                )
                break
        return results

    async def run_pre(self, ctx: GuardContext) -> list[GuardResult]:
        """The ``pre`` guards (1-3) configured on this chain."""
        return await self.run(ctx, stages=_PRE_STAGES)

    async def run_post(self, ctx: GuardContext) -> list[GuardResult]:
        """Guard 4 and then any ``state`` guard configured on this chain: H2
        configures guards 1-5 and runs RulesGuard (rules 1, 7, 8) right after
        OutputShapeGuard (design B §5.1)."""
        return await self.run(ctx, stages=_POST_STAGES)
