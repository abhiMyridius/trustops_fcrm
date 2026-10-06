"""What an agent was given when its job failed, kept so a person can finish it.

H2 writes one :class:`AgentRawInputs` document per job that fails after its retry
(``agent_raw_inputs`` in the harness database, ``_id`` = the job id) and names it in
``AgentFailed.raw_inputs_ref``. H1 reads it when it opens the ``handoff`` gate and
puts a bounded rendering on the gate's subject, so the analyst sees what the agent
saw (the context pack as the model received it) before finishing the step by hand.

The text is the pack's sections in order (``## name`` headings): the case data, the
sources and the policy the agent was shown. It is for the analyst only; the PO fence
has no place for it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Optional

from pydantic import BaseModel, ConfigDict, Field

#: The collection (in the harness database) H2 writes and H1 reads.
RAW_INPUTS_COLLECTION: Final = "agent_raw_inputs"

#: The longest text H2 stores for one job (a pack is token-capped well under it).
RAW_INPUTS_MAX_CHARS: Final = 200_000

#: The longest text H1 puts on a gate (and so on the ``gate.opened`` event).
GATE_INPUTS_MAX_CHARS: Final = 100_000


def raw_inputs_ref(job_id: str) -> str:
    """The reference ``AgentFailed.raw_inputs_ref`` carries for ``job_id``:
    collection and id, so a reader needs no other knowledge to find it.
    """
    return f"{RAW_INPUTS_COLLECTION}/{job_id}"


class AgentRawInputs(BaseModel):
    """The inputs of one failed job.

    Attributes:
        job_id: The failed job (the document's ``_id``).
        case_id: The case.
        run_id: The run.
        agent: The agent, as its name.
        job: The ``agent.run`` payload the job was dispatched with.
        text: The context pack's sections as one Markdown text, cut to
            :data:`RAW_INPUTS_MAX_CHARS`; empty when no pack could be built.
        truncated: ``text`` was cut.
        pack_hash: The pack's own hash, ``None`` when no pack was built.
        sections: The section names, in order.
        error: Why no pack was built, when that is why the job failed.
        saved_at: When H2 stored it.
    """

    model_config = ConfigDict(extra="forbid")

    job_id: str
    case_id: str
    run_id: str
    agent: str
    job: dict[str, Any]
    text: str = ""
    truncated: bool = False
    pack_hash: Optional[str] = None
    sections: list[str] = Field(default_factory=list)
    error: Optional[str] = None
    saved_at: datetime
