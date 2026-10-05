"""The actual output of one agent run on one row, and its (de)serialisation.

:class:`AgentOutput` groups the proposal models one agent call returned for
one row (research returns several ``ClauseProposal`` and ``PrecedentMatch``
items; screening returns one ``ScreeningProposal``). It is an eval-side
envelope only: every item inside it is a real ``fcrm_contracts.proposals``
model, and the allowed item types per agent are Guard 4's own
``AGENT_OUTPUT_TYPES`` table, so no shape is redefined here.

Recorded outputs (live runs, replay fixtures) are stored as JSON Lines of
``{"row_id", "producer", "items": [{"type", "data"}]}`` — the same tagged
form as a row's ``expected.reference_outputs``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Sequence
from pathlib import Path

from fcrm_contracts.enums import AgentName
from fcrm_guardrails.output_shape import AGENT_OUTPUT_TYPES
from pydantic import BaseModel, ConfigDict, Field, SerializeAsAny

from fcrm_evals.rows import ReferenceItem

logger = logging.getLogger(__name__)


class UnknownOutputTypeError(ValueError):
    """A tagged item names a type the agent may not emit (Guard 4's table)."""


class AgentOutput(BaseModel):
    """What one agent call produced for one row."""

    model_config = ConfigDict(extra="forbid")

    row_id: str
    producer: str
    items: list[SerializeAsAny[BaseModel]] = Field(default_factory=list)

    def of_type[T: BaseModel](self, kind: type[T]) -> list[T]:
        """The items that are exactly ``kind`` (subclasses are not the
        contract, mirroring Guard 4's exact-type check)."""
        return [item for item in self.items if type(item) is kind]


def allowed_types(agent: AgentName) -> dict[str, type[BaseModel]]:
    """``{class name: class}`` of the proposal models ``agent`` may emit."""
    return {kind.__name__: kind for kind in AGENT_OUTPUT_TYPES[agent]}


def decode_items(agent: AgentName, items: Iterable[ReferenceItem]) -> list[BaseModel]:
    """Validate tagged items into the agent's contract models.

    Raises:
        UnknownOutputTypeError: an item's ``type`` is not one ``agent`` emits.
    """
    registry = allowed_types(agent)
    decoded: list[BaseModel] = []
    for item in items:
        kind = registry.get(item.type)
        if kind is None:
            msg = f"{agent.value} does not emit {item.type}"
            raise UnknownOutputTypeError(msg)
        decoded.append(kind.model_validate(item.data))
    return decoded


def encode_item(model: BaseModel) -> ReferenceItem:
    """Tag ``model`` with its class name for storage."""
    return ReferenceItem(type=type(model).__name__, data=model.model_dump(mode="json"))


def dump_outputs(outputs: Sequence[AgentOutput], path: Path) -> None:
    """Write ``outputs`` as JSON Lines (the recorded-output format)."""
    lines = []
    for output in outputs:
        record = {
            "row_id": output.row_id,
            "producer": output.producer,
            "items": [encode_item(i).model_dump(mode="json") for i in output.items],
        }
        lines.append(json.dumps(record, sort_keys=True, ensure_ascii=False))
    path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")


def load_outputs(agent: AgentName, path: Path) -> list[AgentOutput]:
    """Read recorded outputs written by :func:`dump_outputs` (or by a live
    run), re-validating every item into its contract model."""
    outputs: list[AgentOutput] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            record = json.loads(line)
            items = [ReferenceItem.model_validate(i) for i in record["items"]]
            outputs.append(
                AgentOutput(
                    row_id=record["row_id"],
                    producer=record["producer"],
                    items=decode_items(agent, items),
                )
            )
    return outputs
