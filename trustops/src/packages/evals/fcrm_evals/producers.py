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

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Protocol, runtime_checkable

from pydantic import BaseModel

from fcrm_evals.degrade import degrade
from fcrm_evals.outputs import AgentOutput, decode_items
from fcrm_evals.rows import EvalRow

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


async def produce_all(
    producer: OutputProducer, rows: Sequence[EvalRow]
) -> list[AgentOutput]:
    """One output per row, in row order (sequential: a live producer is
    rate-limited by the model tier, and order keeps results reproducible)."""
    outputs = []
    for row in rows:
        outputs.append(await producer.produce(row))
    logger.info("%s produced %d outputs", producer.name, len(outputs))
    return outputs
