"""Token, cost and latency accounting for a live eval run.

Three layers, all plain data so a result file can carry them:

- :class:`PriceTable`: the USD-per-million-token rows of
  ``ai/models/tiers.yaml`` (``prices_usd_per_mtok``), plus a *label* for every
  model. The price table is a placeholder until confirmed against the AWS
  bill (the file says so); every cost here is therefore an estimate at those
  list prices, never an invoice figure.
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
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final, Optional

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

TOKENS_PER_PRICE_UNIT: Final[float] = 1_000_000.0
"""``tiers.yaml`` prices are USD per this many tokens."""
UNKNOWN_MODEL_LABEL: Final[str] = "unlabelled model"


class ModelPrice(BaseModel):
    """USD per million tokens for one model (``None``: not published)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input: float = Field(ge=0.0)
    output: float = Field(default=0.0, ge=0.0)
    cache_read: Optional[float] = Field(default=None, ge=0.0)
    cache_write: Optional[float] = Field(default=None, ge=0.0)


@dataclass(frozen=True)
class PriceTable:
    """Prices and labels for the models in ``tiers.yaml``.

    ``prices`` and ``labels`` are keyed by model id; ``slots`` maps a model id
    to the tier slots that use it (``mid.primary``) and is the label of last
    resort.
    """

    prices: Mapping[str, ModelPrice]
    labels: Mapping[str, str]
    slots: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def from_tiers(cls, config: Mapping[str, Any]) -> PriceTable:
        """Read ``prices_usd_per_mtok.rows``, ``models.<id>.label`` and the
        tier chains out of a parsed ``tiers.yaml``."""
        rows: Mapping[str, Any] = config.get("prices_usd_per_mtok", {}).get("rows", {})
        prices = {
            model_id: ModelPrice.model_validate(
                {k: v for k, v in row.items() if v is not None}
            )
            for model_id, row in rows.items()
        }
        models: Mapping[str, Any] = config.get("models", {})
        labels = {
            model_id: str(entry["label"])
            for model_id, entry in models.items()
            if isinstance(entry, Mapping) and entry.get("label")
        }
        slots: dict[str, list[str]] = {}
        for tier, entry in config.get("tiers", {}).items():
            chain = [("primary", entry["primary"])] + [
                (f"fallback{i}", model_id)
                for i, model_id in enumerate(entry.get("fallbacks", []), start=1)
            ]
            for slot, model_id in chain:
                slots.setdefault(model_id, []).append(f"{tier}.{slot}")
        return cls(prices, labels, {k: "+".join(v) for k, v in slots.items()})

    def label(self, model_id: str) -> str:
        """The reportable name of ``model_id`` (never the id itself)."""
        return (
            self.labels.get(model_id)
            or self.slots.get(model_id)
            or (UNKNOWN_MODEL_LABEL)
        )

    def redact(self, text: str) -> str:
        """``text`` with every model id it contains replaced by that model's
        label. An error message from Bedrock can name the model it came from;
        the files a run commits must not (module docstring)."""
        known = {*self.prices, *self.labels, *self.slots}
        for model_id in sorted(known, key=len, reverse=True):
            text = text.replace(model_id, self.label(model_id))
        return text

    def cost(
        self,
        model_id: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """Estimated USD of one call. An unpriced model costs 0 and is
        logged once per call so a missing row is visible, not silent."""
        price = self.prices.get(model_id)
        if price is None:
            logger.warning("no price row for %s; its cost is counted as 0", model_id)
            return 0.0
        uncached = max(0, input_tokens - cache_read_tokens - cache_write_tokens)
        amount = uncached * price.input + output_tokens * price.output
        amount += cache_read_tokens * (
            price.cache_read if price.cache_read is not None else price.input
        )
        amount += cache_write_tokens * (
            price.cache_write if price.cache_write is not None else price.input
        )
        return amount / TOKENS_PER_PRICE_UNIT


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
    def peak_attempt_tokens(self) -> int:
        """The largest single attempt, the figure a per-run budget must hold."""
        return max(self.attempt_tokens, default=0)

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
