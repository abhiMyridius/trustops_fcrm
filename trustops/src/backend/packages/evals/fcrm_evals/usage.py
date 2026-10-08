"""Token, cost and latency accounting for a live eval run.

Three layers, all plain data so a result file can carry them:

- :class:`PriceTable`: the USD-per-million-token rows of
  ``ai/models/tiers.yaml`` (``prices_usd_per_mtok``), plus a *label* for every
  model. It lives in :mod:`fcrm_agents.pricing` (ADR-72, so the agent worker
  can price live jobs too) and is re-exported here. Every cost is an estimate
  at list prices, never an invoice figure.
- :class:`RowRun`: what one dataset row cost and how its run ended (status,
  attempts, tokens, guard trail). A blocked or failed row carries an empty
  output plus this record, never a made-up output.
- :class:`RunUsage`: the roll-up the dashboard and ``gate.json`` show.

**Model ids never leave this module.** Committed result files live under
``evals/`` where the repository's lock test forbids model-id shapes
(ADR-04), so every model is reported by the ``label`` that ``tiers.yaml``
gives it (``models.<id>.label``), falling back to its tier slot
(``mid.primary``). The id is only used as a lookup key in memory.
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Optional

# The price table moved to fcrm_agents.pricing (ADR-72); re-exported here so
# every existing caller keeps importing it from this module.
from fcrm_agents.pricing import (
    STATUS_PARTLY_VERIFIED,
    STATUS_UNKNOWN,
    STATUS_UNVERIFIED,
    STATUS_VERIFIED,
    TOKENS_PER_PRICE_UNIT,
    UNKNOWN_MODEL_LABEL,
    ModelPrice,
    PriceTable,
)
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

__all__ = [
    "STATUS_PARTLY_VERIFIED",
    "STATUS_UNKNOWN",
    "STATUS_UNVERIFIED",
    "STATUS_VERIFIED",
    "TOKENS_PER_PRICE_UNIT",
    "UNKNOWN_MODEL_LABEL",
    "ModelPrice",
    "PriceTable",
    "RowRun",
    "RowStatus",
    "RunUsage",
    "UsageMeter",
    "usage_of_rows",
]


class RowStatus(StrEnum):
    """How one row's agent run ended."""

    OK = "ok"
    BLOCKED = "blocked"
    FAILED = "failed"


class GuardTrace(BaseModel):
    """One guard verdict on the way through a row's run."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    guard: str
    status: str
    attempt: Optional[int] = None


class RowRun(BaseModel):
    """What one live run of one row cost and how it ended."""

    model_config = ConfigDict(extra="forbid")

    row_id: str
    status: RowStatus
    verdict: Optional[str] = None
    stopped_at: Optional[str] = None
    reason: Optional[str] = None
    attempts: int = 0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    models: list[str] = Field(default_factory=list)
    items_accepted: int = 0
    items_dropped: int = 0
    claims_attempt1: Optional[int] = None
    verified_attempt1: Optional[int] = None
    guards: list[GuardTrace] = Field(default_factory=list)
    feedback: list[str] = Field(default_factory=list)
    attempt_tokens: list[int] = Field(default_factory=list)
    """Tokens (input plus output, every request) each attempt used: what the
    tier's per-run budget limits. A budget is set from the p90 of these."""
    tools_called: list[str] = Field(default_factory=list)
    """The tools the model called, over every attempt, in order."""

    @property
    def retries(self) -> int:
        """Attempts beyond the first."""
        return max(0, self.attempts - 1)

    @property
    def total_tokens(self) -> int:
        """Input plus output tokens."""
        return self.input_tokens + self.output_tokens


class RunUsage(BaseModel):
    """The roll-up of a set of calls: an agent's rows, or a judge's calls."""

    model_config = ConfigDict(extra="forbid")

    calls: int = 0
    retries: int = 0
    blocked_rows: int = 0
    failed_rows: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    latency_p50_s: Optional[float] = None
    latency_max_s: Optional[float] = None
    models: list[str] = Field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        """Input plus output tokens."""
        return self.input_tokens + self.output_tokens

    def plus(self, other: RunUsage) -> RunUsage:
        """Sum of two usages; latency percentiles are not summable and are
        taken as the larger of the two."""
        return RunUsage(
            calls=self.calls + other.calls,
            retries=self.retries + other.retries,
            blocked_rows=self.blocked_rows + other.blocked_rows,
            failed_rows=self.failed_rows + other.failed_rows,
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            latency_p50_s=_larger(self.latency_p50_s, other.latency_p50_s),
            latency_max_s=_larger(self.latency_max_s, other.latency_max_s),
            models=sorted({*self.models, *other.models}),
        )


def _larger(a: Optional[float], b: Optional[float]) -> Optional[float]:
    present = [v for v in (a, b) if v is not None]
    return max(present) if present else None


def percentile(values: Sequence[float], fraction: float) -> Optional[float]:
    """The ``fraction`` quantile by the nearest-rank method (``None`` when
    there are no values); ``0.5`` is the median the dashboard shows."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(fraction * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


def usage_of_rows(rows: Iterable[RowRun]) -> RunUsage:
    """Roll a run's :class:`RowRun` records up into a :class:`RunUsage`."""
    records = list(rows)
    latencies = [r.latency_s for r in records if r.latency_s > 0]
    return RunUsage(
        calls=sum(r.calls for r in records),
        retries=sum(r.retries for r in records),
        blocked_rows=sum(1 for r in records if r.status is RowStatus.BLOCKED),
        failed_rows=sum(1 for r in records if r.status is RowStatus.FAILED),
        input_tokens=sum(r.input_tokens for r in records),
        output_tokens=sum(r.output_tokens for r in records),
        cache_read_tokens=sum(r.cache_read_tokens for r in records),
        cache_write_tokens=sum(r.cache_write_tokens for r in records),
        cost_usd=round(sum(r.cost_usd for r in records), 6),
        latency_p50_s=percentile(latencies, 0.5),
        latency_max_s=max(latencies) if latencies else None,
        models=sorted({m for r in records for m in r.models}),
    )


@dataclass(frozen=True)
class CallRecord:
    """One model request: what it used and how long it took."""

    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    latency_s: float


@dataclass
class UsageMeter:
    """Collects the :class:`CallRecord` of every request made through it.

    One meter per row for agent runs (so a row's tokens are exactly its own,
    even under concurrency) and one for the whole judge (its calls are not
    attributable to one row, and are reported as a single roll-up).
    """

    prices: PriceTable
    calls: list[CallRecord] = field(default_factory=list)

    def record(
        self,
        model_id: str,
        *,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        latency_s: float = 0.0,
    ) -> None:
        """Add one request."""
        self.calls.append(
            CallRecord(
                model=model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                latency_s=latency_s,
            )
        )

    @property
    def input_tokens(self) -> int:
        """Total input tokens so far."""
        return sum(c.input_tokens for c in self.calls)

    @property
    def output_tokens(self) -> int:
        """Total output tokens so far."""
        return sum(c.output_tokens for c in self.calls)

    @property
    def cost_usd(self) -> float:
        """Estimated USD so far, at the price table's list prices."""
        return sum(
            self.prices.cost(
                c.model,
                input_tokens=c.input_tokens,
                output_tokens=c.output_tokens,
                cache_read_tokens=c.cache_read_tokens,
                cache_write_tokens=c.cache_write_tokens,
            )
            for c in self.calls
        )

    @property
    def model_labels(self) -> list[str]:
        """Labels (never ids) of every model that answered."""
        return sorted({self.prices.label(c.model) for c in self.calls})

    def as_usage(self) -> RunUsage:
        """This meter as a :class:`RunUsage` (a judge's roll-up)."""
        latencies = [c.latency_s for c in self.calls if c.latency_s > 0]
        return RunUsage(
            calls=len(self.calls),
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cache_read_tokens=sum(c.cache_read_tokens for c in self.calls),
            cache_write_tokens=sum(c.cache_write_tokens for c in self.calls),
            cost_usd=round(self.cost_usd, 6),
            latency_p50_s=percentile(latencies, 0.5),
            latency_max_s=max(latencies) if latencies else None,
            models=self.model_labels,
        )
