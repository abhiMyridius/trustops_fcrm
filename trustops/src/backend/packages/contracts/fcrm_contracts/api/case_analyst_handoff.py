"""S1's analyst handoff routes: the way out of a run that an agent could not finish
(flow F-path).

An agent that fails after its one retry sends the run to ``handoff`` and opens a
``handoff`` gate for the analyst, one per failed job. These shapes are what the
analyst reads and posts to finish that job by hand:

* ``GET /cases/{id}/handoff/gates`` lists the gates (:class:`HandoffGatesView`),
  each naming the failed job, why it failed and where the run was.
* ``POST /cases/{id}/handoff/{gate_id}/complete`` takes the proposals the agent
  should have produced and a reason (:class:`HandoffCompleteBody`). S1 checks them
  like an agent's (the same proposal contracts and Guard 4's source-free checks,
  ``fcrm_guardrails.manual_output``), stores them and the decision, and only then
  announces ``gate.decided handoff`` with the output attached; Caseflow checks them
  again and treats them exactly like the agent's ``agent.done`` with actor
  ``human``.

The output is never a score, band or rating: those fields do not exist on any
proposal contract, so a stray ``score`` key is refused by the model. Nothing here
is visible to the PO (the PO fence has no handoff, gate or harness data).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Final, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: The shortest reason a handoff may carry: a person replacing the machine says why.
HANDOFF_REASON_MIN_CHARS: Final = 20
#: Longest reason accepted.
HANDOFF_REASON_MAX_CHARS: Final = 2000
#: The most proposals one handoff may carry (the same bound the check applies).
HANDOFF_MAX_ITEMS: Final = 200


class HandoffCompleteBody(BaseModel):
    """``POST /cases/{id}/handoff/{gate_id}/complete``: the proposals to stand in
    for the failed job's output, and why a person is supplying them.

    ``output`` is the JSON an agent's ``accepted`` list carries: each item is one
    of the proposal models the failed agent may emit. An empty list is accepted
    only for an agent whose contract allows an empty result (the critic, the
    watchlist, the policy-gap agent); the check says which.
    """

    model_config = ConfigDict(extra="forbid")

    output: list[dict[str, Any]] = Field(max_length=HANDOFF_MAX_ITEMS)
    reason: str = Field(
        min_length=HANDOFF_REASON_MIN_CHARS, max_length=HANDOFF_REASON_MAX_CHARS
    )

    @field_validator("reason")
    @classmethod
    def _enough_after_trimming(cls, value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) < HANDOFF_REASON_MIN_CHARS:
            raise ValueError(
                f"a handoff needs a reason of at least {HANDOFF_REASON_MIN_CHARS} "
                "characters"
            )
        return trimmed


class HandoffCompleteResult(BaseModel):
    """What completing a handoff answers (202): the gate, the job it finished, how
    many proposals were accepted and whether this call changed nothing because the
    same output had already been recorded.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    gate_id: str
    agent: str
    job_id: str
    items: int
    decided_by: str
    replayed: bool = False


class HandoffGateView(BaseModel):
    """One failed job waiting for the analyst: what failed, how, and where the run
    stood. ``reason`` is the failure code and text H2 reported. ``raw_inputs`` is
    what the agent was given (the context pack as the model received it; cut,
    ``raw_inputs_truncated`` says so) while the gate is open, ``raw_inputs_ref``
    names the stored copy and ``raw_inputs_sha256`` is the digest the audit record
    carries; ``raw_inputs_error`` says why there are none.
    """

    model_config = ConfigDict(extra="forbid")

    gate_id: str
    agent: str
    job_id: str
    step: Optional[str] = None
    reason: str
    attempt: int
    raw_inputs_ref: Optional[str] = None
    raw_inputs: Optional[str] = None
    raw_inputs_sha256: Optional[str] = None
    raw_inputs_truncated: bool = False
    raw_inputs_error: Optional[str] = None
    opened_at: datetime
    status: Literal["open", "decided"]


class HandoffGatesView(BaseModel):
    """``GET /cases/{id}/handoff/gates``: the case's handoff gates, oldest first,
    open ones and decided ones, so the page can show what was finished by hand.
    """

    model_config = ConfigDict(extra="forbid")

    case_id: str
    gates: list[HandoffGateView]
