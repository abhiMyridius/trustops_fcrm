"""The dataset row: one evaluation case for one agent.

Every bundled ``.jsonl`` file under ``fcrm_evals/datasets/data`` holds one
:class:`EvalRow` per line. A row carries:

- ``input``: the context the agent would see. ``input["sources"]`` is the
  list of citable sources (``fcrm_guardrails.context.AvailableSource``
  dumps): exactly what Guard 4 resolves ``source_ref`` values against, so
  ``citation_validity`` here and Guard 4 in production check the same thing.
- ``expected``: ``labels`` (what the metrics compare against) and
  ``reference_outputs`` (a correct output expressed in the agent's real
  ``fcrm_contracts.proposals`` models, used by the echo self-check and as
  the replay fixture shape).
- ``rubric_notes``: where the expected values came from. When the seed does
  not carry an expected value explicitly and the generator had to
  synthesize one, the note says so.
- ``metadata.source``: ``seed`` / ``demo`` / ``synthetic`` (design B §7.2's
  provenance vocabulary; ``harvested`` is reserved for analyst overrides).
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import Any, Optional

from fcrm_contracts.enums import AgentName
from fcrm_guardrails.context import AvailableSource
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class RowSource(StrEnum):
    """Where a row's expected values come from (design B §7.2)."""

    SEED = "seed"
    DEMO = "demo"
    SYNTHETIC = "synthetic"
    HARVESTED = "harvested"


class ReferenceItem(BaseModel):
    """One proposal model, tagged with its contract class name so it can be
    re-validated into ``fcrm_contracts.proposals`` without guessing."""

    model_config = ConfigDict(extra="forbid")

    type: str
    data: dict[str, Any]


class Expected(BaseModel):
    """What a correct run of the agent on this row produces."""

    model_config = ConfigDict(extra="forbid")

    labels: dict[str, Any]
    reference_outputs: list[ReferenceItem] = Field(default_factory=list)


class RowMeta(BaseModel):
    """Provenance and tags; ``label_conflict`` records a known disagreement
    between two seed-derived labels (kept visible, never silently resolved).
    """

    model_config = ConfigDict(extra="forbid")

    source: RowSource
    tags: list[str] = Field(default_factory=list)
    label_conflict: Optional[str] = None


class EvalRow(BaseModel):
    """One evaluation case (see the module docstring)."""

    model_config = ConfigDict(extra="forbid")

    row_id: str
    case_id: str
    agent: AgentName
    input: dict[str, Any]
    expected: Expected
    rubric_notes: str
    metadata: RowMeta

    def sources(self) -> list[AvailableSource]:
        """``input["sources"]`` re-validated as Guard 4's source objects."""
        raw = self.input.get("sources", [])
        return [AvailableSource.model_validate(item) for item in raw]

    def label(self, name: str, default: Any = None) -> Any:
        """``expected.labels[name]``, or ``default`` when absent."""
        return self.expected.labels.get(name, default)
