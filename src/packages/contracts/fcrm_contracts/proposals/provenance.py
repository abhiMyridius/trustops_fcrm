"""The provenance contract every agent output obeys.

Guard 4 (output shape) checks that every ``Claim`` in a proposal has a
``source_ref`` that resolves and a ``quote`` that exists verbatim in the
cited source. This is rule 8 ("policy is quoted, not generated") made
structural rather than merely policed by review. See design B §2 and
fcrm_guardrails.quote_match for the matching algorithm.
"""

from __future__ import annotations

from typing import Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class DocSourceRef(BaseModel):
    """A span inside an uploaded, ingested document."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["doc"] = "doc"
    doc_id: str
    page: int
    line_start: int
    line_end: int


class ClauseSourceRef(BaseModel):
    """A clause in the policy corpus."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["clause"] = "clause"
    clause_id: str
    corpus_v: int


class RecordSourceRef(BaseModel):
    """An existing audit record (used by the examiner agent, which may only
    cite the hash-chained ledger).
    """

    model_config = ConfigDict(extra="forbid")

    kind: Literal["record"] = "record"
    case_id: str
    seq: int


class CaseSourceRef(BaseModel):
    """A precedent case (used by research/precedent matching)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["case"] = "case"
    case_id: str


class ControlSourceRef(BaseModel):
    """A control inventory entry."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["control"] = "control"
    control_id: str
    controls_v: int


class OwnerAnswerSourceRef(BaseModel):
    """A PO's free-text answer to a clarification question."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["owner_answer"] = "owner_answer"
    thread_id: str
    question_id: str


class ListSourceRef(BaseModel):
    """A jurisdiction or sanctions list entry (watchlist agent)."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["list"] = "list"
    list_id: str
    version: str


SourceRef = Union[
    DocSourceRef,
    ClauseSourceRef,
    RecordSourceRef,
    CaseSourceRef,
    ControlSourceRef,
    OwnerAnswerSourceRef,
    ListSourceRef,
]


class Claim(BaseModel):
    """Mixed into every agent output field that asserts a fact. ``quote``
    must appear verbatim (after the normalisation in quote_match.py) in the
    text the ``source_ref`` points to; ``confidence`` is the agent's own
    self-estimate and is never used as a gate by itself.
    """

    model_config = ConfigDict(extra="forbid")

    source_ref: SourceRef = Field(discriminator="kind")
    quote: str
    confidence: float = Field(ge=0.0, le=1.0)


class Searched(BaseModel):
    """The structural counterpart to a ``Claim``: proof that an agent
    actually looked for something before reporting it absent, rather than
    silently assuming a gap. ``ai/CLAUDE.md``: "Give every absence a
    Searched block." Used wherever an agent reports "no X found" (a
    missing policy clause, no matching precedent) so that claim and
    gap are equally auditable.
    """

    model_config = ConfigDict(extra="forbid")

    query: str
    indexes: list[str]
    hits: int = Field(ge=0)
