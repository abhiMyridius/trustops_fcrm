"""Which rows of a dataset a sampled live run scores.

A live run costs tokens, so ``--sample N --seed S`` scores ``N`` rows per
agent instead of the whole dataset. The slice is chosen so that a handful of
rows still says something:

1. **The demo-case rows are always in.** FCRM-2026-0151 is the case every
   figure in the demo comes from; a sampled run that skipped it would not
   measure the agents on the case the panel watches.
2. **The rest are drawn by a seeded RNG, stratified where the metrics need
   it.** Precision is undefined on a slice with no predicted positive and
   recall on a slice with no actual positive, so datasets with a rare class
   (screening's 18 fast-lane rows in 100, watchlist's 3 re-flag rows in 29,
   examiner's unanswerable questions) are drawn round-robin across their
   classes instead of uniformly. Without this a 5-row slice of screening
   would usually contain no positive row at all and its gate would read "not
   measured" for a reason that has nothing to do with the agent.
3. **The same seed gives the same rows**, whatever the dataset's size, so a
   run can be repeated and two runs compared.

``--row-ids`` replaces the draw: exactly those rows, in dataset order.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Hashable, Sequence
from typing import Final, Optional

from fcrm_contracts.enums import AgentName
from pydantic import BaseModel, ConfigDict, Field

from fcrm_evals.rows import EvalRow, RowSource

logger = logging.getLogger(__name__)

DEMO_CASE_ID: Final[str] = "FCRM-2026-0151"
"""The canonical demo case; every row about it is a demo row."""


class SampleInfo(BaseModel):
    """What was sampled, recorded in the result so a run can be repeated."""

    model_config = ConfigDict(extra="forbid")

    n: int
    of: int
    seed: Optional[int] = None
    strategy: str
    row_ids: list[str] = Field(default_factory=list)
    demo_row_ids: list[str] = Field(default_factory=list)


class UnknownRowIdError(LookupError):
    """``--row-ids`` named a row the dataset does not have."""


def is_demo_row(row: EvalRow) -> bool:
    """True for a row about the demo case, or sourced from the demo."""
    return row.case_id == DEMO_CASE_ID or row.metadata.source is RowSource.DEMO


def _by_screening(row: EvalRow) -> Hashable:
    return bool(row.label("fast_lane"))


def _by_answerable(row: EvalRow) -> Hashable:
    return bool(row.label("answerable"))


def _by_reflag(row: EvalRow) -> Hashable:
    return bool(row.label("reflags"))


def _by_planted_error(row: EvalRow) -> Hashable:
    expected = row.label("expected_findings", [])
    return tuple(sorted(str(f.get("type")) for f in expected))


STRATA: Final[dict[AgentName, Callable[[EvalRow], Hashable]]] = {
    AgentName.SCREENING: _by_screening,
    AgentName.EXAMINER: _by_answerable,
    AgentName.WATCHLIST: _by_reflag,
    AgentName.CRITIC: _by_planted_error,
}
"""Per agent, the label a draw is spread across. An agent absent from this
table is drawn uniformly."""


def _round_robin(
    pool: Sequence[EvalRow],
    wanted: int,
    stratum: Optional[Callable[[EvalRow], Hashable]],
    rng: random.Random,
) -> list[EvalRow]:
    shuffled = list(pool)
    rng.shuffle(shuffled)
    if stratum is None or wanted <= 0:
        return shuffled[: max(wanted, 0)]
    buckets: dict[Hashable, list[EvalRow]] = {}
    for row in shuffled:
        buckets.setdefault(stratum(row), []).append(row)
    # The rarest class first, so a one-row draw still shows the class that
    # makes the metric defined.
    queues = sorted(buckets.values(), key=len)
    picked: list[EvalRow] = []
    while len(picked) < wanted and any(queues):
        for queue in queues:
            if queue and len(picked) < wanted:
                picked.append(queue.pop())
    return picked


def sample_rows(
    agent: AgentName,
    rows: Sequence[EvalRow],
    *,
    n: Optional[int] = None,
    seed: int = 0,
    row_ids: Optional[Sequence[str]] = None,
) -> tuple[list[EvalRow], SampleInfo]:
    """The rows to score and a record of how they were chosen.

    Args:
        agent: The agent whose dataset ``rows`` is (selects the stratum).
        rows: The whole dataset, in dataset order.
        n: Rows to score; ``None`` scores them all. Demo rows count towards
            ``n``, and if there are more demo rows than ``n`` they are all
            kept (the demo case is never dropped).
        seed: Seeds the draw; the same seed gives the same rows.
        row_ids: Score exactly these rows instead of drawing.

    Returns:
        ``(rows in dataset order, SampleInfo)``.

    Raises:
        UnknownRowIdError: ``row_ids`` names a row this dataset lacks.
    """
    by_id = {row.row_id: row for row in rows}
    if row_ids:
        missing = [rid for rid in row_ids if rid not in by_id]
        if missing:
            msg = f"{agent.value} has no row(s) {missing}"
            raise UnknownRowIdError(msg)
        wanted = set(row_ids)
        chosen = [row for row in rows if row.row_id in wanted]
        strategy = "row_ids"
    elif n is None or n >= len(rows):
        chosen = list(rows)
        strategy = "all"
    else:
        demo = [row for row in rows if is_demo_row(row)]
        rest = [row for row in rows if not is_demo_row(row)]
        rng = random.Random(f"{seed}:{agent.value}")
        drawn = _round_robin(rest, n - len(demo), STRATA.get(agent), rng)
        keep = {row.row_id for row in [*demo, *drawn]}
        chosen = [row for row in rows if row.row_id in keep]
        strategy = "demo+stratified" if agent in STRATA else "demo+uniform"
    info = SampleInfo(
        n=len(chosen),
        of=len(rows),
        seed=None if row_ids or n is None else seed,
        strategy=strategy,
        row_ids=[row.row_id for row in chosen],
        demo_row_ids=[row.row_id for row in chosen if is_demo_row(row)],
    )
    logger.info(
        "%s: sampled %d of %d rows (%s)", agent.value, info.n, info.of, strategy
    )
    return chosen, info
