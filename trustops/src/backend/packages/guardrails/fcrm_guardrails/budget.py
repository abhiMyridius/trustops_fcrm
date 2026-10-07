"""Guard 3: budgets (design B §4.4, design A §3.3, BUILD-PLAN §3.4).

The tier table, the per-case ledger, and the attempt/fallback plan are pure
data structures here so H1 (reserve before dispatch, settle on
``agent.done``) and H2 (per-attempt limits) share one source of truth for
"would this call exceed budget". :class:`CaseBudget` is the in-memory
mirror of one ``fcrm_harness.budgets`` document; H1 loads it with
:meth:`CaseBudget.from_snapshot`, mutates it inside the transition's Mongo
transaction, and persists :meth:`CaseBudget.snapshot`.

Budget table (BUILD-PLAN §3.4 priced it cheap 6k, mid 12k, strong 20k, judge 20k;
the first live run showed a run costs more, and the tokens are now set from
measured usage, about 30% over the p90 of an attempt, in ``ai/models/tiers.yaml``,
which ``tests/unit/packages/agents/test_budgets_agree.py`` keeps equal to this
table): cheap 15k tokens / 60 s, mid 15k / 120 s, strong 24k / 180 s, judge 20k /
180 s (the seed's
"strong + judge"), one retry each. A case may spend 160k tokens (the enforced
cap, :data:`CASE_TOKEN_CAP`: the worst case measured on the demo case, a run in
which Guard 4 forced a retry, plus a small margin); 120k is the target a clean run
fits in (:data:`CASE_TOKEN_TARGET`), which the harness monitor reports against.
H1 enforces the cap across a case's jobs with its own ledger
(``fcrm_caseflow.budget``: one attempt of the tier reserved per dispatch, the
real usage settled per result); :class:`CaseBudget` below is H2's per-job view.

Fallback order (design B §4.4): attempt 1 runs the tier's
``FallbackModel(primary, fallback)`` (transport errors fall through to the
fallback); attempt 2 runs the fallback model alone with the tier's own
``max_tokens`` (it was halved before the first live run: Sonnet 5.5 and Opus 5.5
think adaptively and cannot switch it off, and half an allowance went on
thinking before the forced tool call, so a retried row returned an empty call);
there is no attempt 3: a second failure opens the human ``handoff`` gate.
Model ids are never named here (ADR-04): only slots.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from fcrm_contracts.enums import AgentName, Tier
from pydantic import BaseModel, ConfigDict, Field

from fcrm_guardrails.base import Guard, GuardName, GuardResult, GuardStatus

if TYPE_CHECKING:
    from fcrm_guardrails.context import GuardContext

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TierBudget:
    """Per-run limits for one tier."""

    tier: Tier
    tokens: int
    wall_s: int
    retries: int = 1

    @property
    def max_attempts(self) -> int:
        """First attempt plus the retries."""
        return 1 + self.retries

    @property
    def reservation_tokens(self) -> int:
        """What H1 reserves against the case cap before dispatch."""
        return self.tokens * self.max_attempts


TIER_BUDGETS: Final[Mapping[Tier, TierBudget]] = {
    Tier.CHEAP: TierBudget(Tier.CHEAP, tokens=15_000, wall_s=60),
    Tier.MID: TierBudget(Tier.MID, tokens=15_000, wall_s=120),
    Tier.STRONG: TierBudget(Tier.STRONG, tokens=24_000, wall_s=180),
    Tier.JUDGE: TierBudget(Tier.JUDGE, tokens=28_000, wall_s=180),
}

CASE_TOKEN_CAP: Final[int] = 160_000
"""Tokens per case across every agent run that H1 enforces (BUILD-PLAN §3.4 priced
120k; the live demo case measured 115k clean and 126k to 158k with a Guard 4
retry, so the enforced cap is the worst case measured plus a small margin)."""

CASE_TOKEN_TARGET: Final[int] = 120_000
"""Tokens a clean run of a case is expected to fit in: the efficiency target the
harness monitor reports (``tokens_target``). A case over it and under
:data:`CASE_TOKEN_CAP` is not an error."""


class BudgetExceededError(Exception):
    """A reservation would take the case past its token cap."""


class AttemptsExhaustedError(Exception):
    """The tier's retries are used up; the next step is the human gate."""


class UnknownReservationError(KeyError):
    """``settle``/``release`` named a reservation this ledger does not hold."""


class ModelSlot(StrEnum):
    """Which configured model an attempt runs on (ids live in tiers.yaml)."""

    PRIMARY_WITH_FALLBACK = "primary_with_fallback"
    FALLBACK_ONLY = "fallback_only"


class AttemptPlan(BaseModel):
    """The limits H2 applies to one attempt."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    attempt: int
    slot: ModelSlot
    token_limit: int
    wall_s: int
    max_tokens_factor: float


def attempt_plan(
    tier: Tier, attempt: int, *, tiers: Mapping[Tier, TierBudget] = TIER_BUDGETS
) -> AttemptPlan:
    """The slot and limits for ``attempt`` (1-based) on ``tier``.

    Raises:
        AttemptsExhaustedError: ``attempt`` is beyond ``1 + retries``.
        ValueError: ``attempt`` is less than 1.
    """
    budget = tiers[tier]
    if attempt < 1:
        msg = f"attempt must be >= 1, got {attempt}"
        raise ValueError(msg)
    if attempt > budget.max_attempts:
        msg = f"{tier.value} allows {budget.max_attempts} attempts; got {attempt}"
        raise AttemptsExhaustedError(msg)
    first = attempt == 1
    return AttemptPlan(
        attempt=attempt,
        slot=ModelSlot.PRIMARY_WITH_FALLBACK if first else ModelSlot.FALLBACK_ONLY,
        token_limit=budget.tokens,
        wall_s=budget.wall_s,
        max_tokens_factor=1.0,
    )


def call_exceeds_tier(
    tier: Tier,
    *,
    tokens_used: int,
    elapsed_s: float,
    tiers: Mapping[Tier, TierBudget] = TIER_BUDGETS,
) -> bool:
    """True when one attempt's actual usage broke its tier limit (H2 checks
    this after the call as a backstop to ``UsageLimits``/``asyncio.timeout``)."""
    budget = tiers[tier]
    return tokens_used > budget.tokens or elapsed_s > budget.wall_s


class Reservation(BaseModel):
    """Tokens held for one dispatched job until it settles."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reservation_id: str
    agent: AgentName
    tier: Tier
    tokens: int


class AgentUsage(BaseModel):
    """Settled usage for one agent within the case."""

    model_config = ConfigDict(extra="forbid")

    runs: int = 0
    tokens: int = 0


class BudgetSnapshot(BaseModel):
    """The persisted shape of :class:`CaseBudget` (``fcrm_harness.budgets``)."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    cap_tokens: int = CASE_TOKEN_CAP
    used_tokens: int = 0
    reservations: dict[str, Reservation] = Field(default_factory=dict)
    per_agent: dict[str, AgentUsage] = Field(default_factory=dict)


class CaseBudget:
    """One case's token ledger: ``used + reserved <= cap`` always holds
    after any successful :meth:`reserve` (settling may overrun, because the
    tokens were really spent; the overrun is logged and blocks the next
    reservation)."""

    def __init__(
        self,
        case_id: str,
        *,
        cap_tokens: int = CASE_TOKEN_CAP,
        tiers: Mapping[Tier, TierBudget] = TIER_BUDGETS,
    ) -> None:
        """An empty ledger for ``case_id``."""
        self.case_id = case_id
        self.cap_tokens = cap_tokens
        self._tiers = tiers
        self._used = 0
        self._reservations: dict[str, Reservation] = {}
        self._per_agent: dict[str, AgentUsage] = {}

    @property
    def used(self) -> int:
        """Settled tokens."""
        return self._used

    @property
    def reserved(self) -> int:
        """Tokens currently held by open reservations."""
        return sum(r.tokens for r in self._reservations.values())

    def tier_budget(self, tier: Tier) -> TierBudget:
        """The per-run limits for ``tier``."""
        return self._tiers[tier]

    def remaining(self) -> int:
        """Tokens neither used nor reserved (never negative)."""
        return max(0, self.cap_tokens - self._used - self.reserved)

    def would_exceed(self, tier: Tier) -> bool:
        """True when reserving one run of ``tier`` would break the cap."""
        return self._tiers[tier].reservation_tokens > self.remaining()

    def has_reservation(self, reservation_id: str) -> bool:
        """True while ``reservation_id`` is held."""
        return reservation_id in self._reservations

    def reserve(self, reservation_id: str, agent: AgentName, tier: Tier) -> Reservation:
        """Hold ``tokens x (1 + retries)`` for a job. Idempotent per id, so a
        redelivered dispatch does not double-reserve.

        Raises:
            BudgetExceededError: the reservation would break the case cap.
        """
        existing = self._reservations.get(reservation_id)
        if existing is not None:
            return existing
        needed = self._tiers[tier].reservation_tokens
        if needed > self.remaining():
            msg = (
                f"case {self.case_id}: {agent.value} ({tier.value}) needs {needed} "
                f"tokens, {self.remaining()} of {self.cap_tokens} left"
            )
            raise BudgetExceededError(msg)
        reservation = Reservation(
            reservation_id=reservation_id, agent=agent, tier=tier, tokens=needed
        )
        self._reservations[reservation_id] = reservation
        return reservation

    def settle(self, reservation_id: str, actual_tokens: int) -> int:
        """Release a reservation and record what the job really used.
        Returns the case's new ``used`` total.

        Raises:
            UnknownReservationError: no such reservation is held.
            ValueError: ``actual_tokens`` is negative.
        """
        if actual_tokens < 0:
            msg = f"actual_tokens must be >= 0, got {actual_tokens}"
            raise ValueError(msg)
        reservation = self._pop(reservation_id)
        if actual_tokens > reservation.tokens:
            logger.warning(
                "agent run overran its reservation",
                extra={
                    "case_id": self.case_id,
                    "reservation_id": reservation_id,
                    "reserved": reservation.tokens,
                    "actual": actual_tokens,
                },
            )
        self._used += actual_tokens
        usage = self._per_agent.setdefault(reservation.agent.value, AgentUsage())
        usage.runs += 1
        usage.tokens += actual_tokens
        return self._used

    def release(self, reservation_id: str) -> None:
        """Drop a reservation without usage (the job was never dispatched).

        Raises:
            UnknownReservationError: no such reservation is held.
        """
        self._pop(reservation_id)

    def _pop(self, reservation_id: str) -> Reservation:
        try:
            return self._reservations.pop(reservation_id)
        except KeyError as exc:
            logger.error(
                "unknown budget reservation",
                extra={"case_id": self.case_id, "reservation_id": reservation_id},
            )
            raise UnknownReservationError(reservation_id) from exc

    def snapshot(self) -> BudgetSnapshot:
        """The persistable state of this ledger."""
        return BudgetSnapshot(
            case_id=self.case_id,
            cap_tokens=self.cap_tokens,
            used_tokens=self._used,
            reservations=dict(self._reservations),
            per_agent={k: v.model_copy() for k, v in self._per_agent.items()},
        )

    @classmethod
    def from_snapshot(
        cls,
        snapshot: BudgetSnapshot,
        *,
        tiers: Mapping[Tier, TierBudget] = TIER_BUDGETS,
    ) -> CaseBudget:
        """Rebuild a ledger from its persisted state."""
        budget = cls(snapshot.case_id, cap_tokens=snapshot.cap_tokens, tiers=tiers)
        budget._used = snapshot.used_tokens
        budget._reservations = dict(snapshot.reservations)
        budget._per_agent = {k: v.model_copy() for k, v in snapshot.per_agent.items()}
        return budget


class BudgetGuard(Guard):
    """Guard 3 (pre; H1 and H2). Fails closed: no ledger, no tier, or no
    attempts left is ``blocked``.

    - H1 (no ``reservation_id`` in the context): passes when one run of the
      tier still fits the case cap; H1 then calls :meth:`CaseBudget.reserve`
      in the same transaction. ``blocked`` means H1 opens the human gate
      ``handoff{reason: budget_exhausted}``.
    - H2 (``reservation_id`` set): passes only when that reservation is held.
    """

    name = GuardName.BUDGET
    stage = "pre"

    async def check(self, ctx: GuardContext) -> GuardResult:
        """Check the case ledger and the attempt count for this job."""
        started = time.perf_counter()
        if ctx.budget is None or ctx.tier is None:
            return self._result(
                GuardStatus.BLOCKED,
                "no budget ledger or tier in context; failing closed",
                started=started,
            )
        budget = ctx.budget
        tier_budget = budget.tier_budget(ctx.tier)
        details: dict[str, object] = {
            "tier": ctx.tier.value,
            "tokens_per_run": tier_budget.tokens,
            "wall_s": tier_budget.wall_s,
            "attempt": ctx.attempt,
            "cap": budget.cap_tokens,
            "used": budget.used,
            "reserved": budget.reserved,
            "remaining": budget.remaining(),
        }
        try:
            plan = attempt_plan(ctx.tier, ctx.attempt, tiers={ctx.tier: tier_budget})
        except AttemptsExhaustedError as exc:
            return self._result(
                GuardStatus.BLOCKED, str(exc), started=started, details=details
            )
        details["slot"] = plan.slot.value
        if ctx.reservation_id is not None:
            if budget.has_reservation(ctx.reservation_id):
                summary = (
                    f"reservation held · {budget.remaining()} of "
                    f"{budget.cap_tokens} case tokens unreserved"
                )
                return self._result(
                    GuardStatus.PASSED, summary, started=started, details=details
                )
            return self._result(
                GuardStatus.BLOCKED,
                f"no budget reservation {ctx.reservation_id} for this job",
                started=started,
                details=details,
            )
        if budget.would_exceed(ctx.tier):
            summary = (
                f"case budget exhausted: {tier_budget.reservation_tokens} needed, "
                f"{budget.remaining()} of {budget.cap_tokens} left"
            )
            return self._result(
                GuardStatus.BLOCKED, summary, started=started, details=details
            )
        summary = (
            f"{tier_budget.reservation_tokens} tokens fit · "
            f"{budget.remaining()} of {budget.cap_tokens} case tokens unreserved"
        )
        return self._result(
            GuardStatus.PASSED, summary, started=started, details=details
        )
