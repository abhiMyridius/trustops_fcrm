"""Where actual outputs come from.

An :class:`OutputProducer` turns a row into an :class:`AgentOutput`:

- :class:`EchoProducer`: the row's own reference outputs, re-validated into
  the contract models. Against its own dataset it must score 1.0 on every
  metric; anything less is a bug in a generator or a metric. This is the
  runner's sanity check, not a measurement of any agent.
- :class:`DegradedProducer`: the reference outputs with deliberate,
  deterministic mistakes (:mod:`fcrm_evals.degrade`), to prove the metrics
  move and the gate refuses a worse version.
- :class:`RecordedProducer`: outputs recorded earlier (a live run or a
  replay fixture), loaded by row id.
- :class:`CallableProducer`: any async ``row -> list[BaseModel]`` callable.
  This is the live seam: the agents lane passes a function that runs the
  real ``fcrm_agents`` runtime (context pack, Guard 1-4, one Pydantic AI
  call on Bedrock) for the row and returns its proposal items.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Optional, Protocol, runtime_checkable

from pydantic import BaseModel

from fcrm_evals.degrade import degrade
from fcrm_evals.outputs import AgentOutput, decode_items
from fcrm_evals.rows import EvalRow
from fcrm_evals.usage import RowRun, RowStatus

logger = logging.getLogger(__name__)


class MissingOutputError(LookupError):
    """A recorded-output set has no output for a dataset row."""


@runtime_checkable
class OutputProducer(Protocol):
    """Produces the actual output for one row."""

    name: str

    async def produce(self, row: EvalRow) -> AgentOutput:
        """The agent output for ``row``."""
        ...


class EchoProducer:
    """Reference outputs echoed back (the self-check)."""

    name = "echo"

    async def produce(self, row: EvalRow) -> AgentOutput:
        """The row's reference outputs as contract models."""
        items = decode_items(row.agent, row.expected.reference_outputs)
        return AgentOutput(row_id=row.row_id, producer=self.name, items=items)


class DegradedProducer:
    """Reference outputs with deterministic, realistic mistakes."""

    name = "degraded"

    async def produce(self, row: EvalRow) -> AgentOutput:
        """The degraded copy of the row's reference outputs."""
        items = decode_items(row.agent, row.expected.reference_outputs)
        return AgentOutput(
            row_id=row.row_id, producer=self.name, items=degrade(row, items)
        )


class RecordedProducer:
    """Outputs recorded earlier, looked up by row id."""

    def __init__(self, outputs: Sequence[AgentOutput], name: str = "recorded") -> None:
        """Index ``outputs`` by row id."""
        self.name = name
        self._by_row: Mapping[str, AgentOutput] = {o.row_id: o for o in outputs}

    async def produce(self, row: EvalRow) -> AgentOutput:
        """The recorded output of ``row``.

        Raises:
            MissingOutputError: nothing was recorded for this row.
        """
        output = self._by_row.get(row.row_id)
        if output is None:
            msg = f"no recorded output for row {row.row_id}"
            raise MissingOutputError(msg)
        return output


class CallableProducer:
    """Wrap an async ``row -> items`` callable (the live-agent seam)."""

    def __init__(
        self, fn: Callable[[EvalRow], Awaitable[Sequence[BaseModel]]], name: str
    ) -> None:
        """Bind ``fn``; ``name`` labels the results (e.g. a model/prompt id)."""
        self.name = name
        self._fn = fn

    async def produce(self, row: EvalRow) -> AgentOutput:
        """Await ``fn(row)`` and wrap its items."""
        items = list(await self._fn(row))
        return AgentOutput(row_id=row.row_id, producer=self.name, items=items)


async def _produce_isolated(
    producer: OutputProducer, row: EvalRow, row_timeout_s: Optional[float]
) -> AgentOutput:
    """``producer.produce(row)``, with any failure turned into a ``failed``
    output so one bad row never costs the rest of a live run.

    The broad catch is deliberate and logged: a live row can fail in ways no
    list of exception types captures (a transport error under the model's own
    retry, a malformed tool result, a pack that overflows its cap), and the
    run must score the other rows and *count* this one (``RunUsage.failed_rows``)
    rather than raise after the tokens are spent.
    """
    try:
        return await asyncio.wait_for(producer.produce(row), timeout=row_timeout_s)
    except Exception as exc:  # noqa: BLE001 - isolate one row's failure from the run
        logger.exception("row %s failed under %s", row.row_id, producer.name)
        reason = f"{type(exc).__name__}: {exc}"[:300]
        return AgentOutput(
            row_id=row.row_id,
            producer=producer.name,
            status=RowStatus.FAILED,
            reason=reason,
            run=RowRun(row_id=row.row_id, status=RowStatus.FAILED, reason=reason),
        )


async def produce_all(
    producer: OutputProducer,
    rows: Sequence[EvalRow],
    *,
    concurrency: int = 1,
    row_timeout_s: Optional[float] = None,
    isolate: bool = False,
    semaphore: Optional[asyncio.Semaphore] = None,
) -> list[AgentOutput]:
    """One output per row, in row order.

    The default is sequential and fails fast, which is what the offline
    producers want (order keeps results reproducible). A live producer passes
    ``isolate=True`` so a failing row becomes a counted ``failed`` output,
    ``concurrency`` to bound how many Bedrock calls are in flight, and
    ``row_timeout_s`` to bound one row's whole run. ``semaphore`` is one
    shared with other callers (a run over nine agents bounds all their
    rows together); it replaces ``concurrency``.
    """
    bound = semaphore or asyncio.Semaphore(max(1, concurrency))

    async def one(row: EvalRow) -> AgentOutput:
        async with bound:
            if isolate:
                return await _produce_isolated(producer, row, row_timeout_s)
            return await producer.produce(row)

    outputs = list(await asyncio.gather(*(one(row) for row in rows)))
    logger.info("%s produced %d outputs", producer.name, len(outputs))
    return outputs
