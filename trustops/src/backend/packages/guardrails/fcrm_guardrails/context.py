"""The context every guard receives: the contract H1 and H2 must satisfy.

:class:`GuardContext` is deliberately one flat object with optional inputs
per guard, so a caller fills only what the guards it runs need, and a guard
whose input is missing fails closed (``blocked``) rather than passing:

=====================  =========================================  ===========
Guard                  Inputs it reads                            Filled by
=====================  =========================================  ===========
1 input_scan           ``input_texts``, ``keep_terms``            H3, H2
2 permit               ``agent``, ``tools``, ``role``             H2
3 budget               ``tier``, ``attempt``, ``budget``,         H1, H2
                       ``reservation_id``
4 output_shape         ``agent``, ``outputs``, ``sources``,       H2
                       ``attempt``, ``grounding_numbers``
5 rules                ``outputs`` + ``sources`` (rules 1, 8) and H2, H1
                       ``rules`` (:class:`RulesContext`, H1)
=====================  =========================================  ===========

Guards 6 and 7 are not context-driven: Guard 6 is the scheduling hook in
:mod:`fcrm_guardrails.critic_hook`, Guard 7 is
:func:`fcrm_guardrails.human_gate.validate_gate_decision`.

``sources`` is the list of everything the agent was shown that it may cite
(:class:`AvailableSource`): H2 builds it from the ContextPack sections and
the tool results of the run. A ``Claim.source_ref`` resolves only if a
source with the same :func:`source_key` is present, so a clause cited at
another corpus version, a page the agent was never shown, or an invented
document id is an unresolved source by construction.
"""

from __future__ import annotations

from typing import Optional, assert_never

from fcrm_contracts.enums import AgentName, Tier
from fcrm_contracts.events.payloads import JobPayload
from fcrm_contracts.proposals.provenance import (
    CaseSourceRef,
    ClauseSourceRef,
    ControlSourceRef,
    DocSourceRef,
    ListSourceRef,
    OwnerAnswerSourceRef,
    RecordSourceRef,
    SourceRef,
)
from pydantic import BaseModel, ConfigDict, Field

from fcrm_guardrails.budget import CaseBudget
from fcrm_guardrails.rules.context import RulesContext


def source_key(ref: SourceRef) -> str:
    """The lookup key a ``SourceRef`` resolves by.

    Document refs key on ``doc_id`` and ``page`` only (the line span is
    checked against that page's text); every other kind keys on its id plus
    its pinned version where it has one.
    """
    match ref:
        case DocSourceRef():
            return f"doc:{ref.doc_id}:p{ref.page}"
        case ClauseSourceRef():
            return f"clause:{ref.clause_id}@{ref.corpus_v}"
        case RecordSourceRef():
            return f"record:{ref.case_id}#{ref.seq}"
        case CaseSourceRef():
            return f"case:{ref.case_id}"
        case ControlSourceRef():
            return f"control:{ref.control_id}@{ref.controls_v}"
        case OwnerAnswerSourceRef():
            return f"owner_answer:{ref.thread_id}/{ref.question_id}"
        case ListSourceRef():
            return f"list:{ref.list_id}@{ref.version}"
    assert_never(ref)


def record_key(case_id: str, seq: int) -> str:
    """The key of audit record ``seq`` of ``case_id`` (examiner/critic refs)."""
    return source_key(RecordSourceRef(case_id=case_id, seq=seq))


class AvailableSource(BaseModel):
    """One citable source the agent was shown, with the text quotes are
    checked against. For a document, ``source_ref`` names the page (its
    line fields are ignored for lookup) and ``text`` is the page text, one
    line per newline, so a cited line span can be bounds-checked."""

    model_config = ConfigDict(extra="forbid")

    source_ref: SourceRef = Field(discriminator="kind")
    text: str

    @property
    def key(self) -> str:
        """:func:`source_key` of this source."""
        return source_key(self.source_ref)


class ScanText(BaseModel):
    """One free-text input for Guard 1 (a document's canonical Markdown, an
    owner answer, or a context-pack section)."""

    model_config = ConfigDict(extra="forbid")

    name: str
    text: str


class GuardContext(BaseModel):
    """Everything a guard may read (see the module docstring's table)."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    case_id: str
    run_id: str
    job_id: Optional[str] = None
    agent: Optional[AgentName] = None
    tier: Optional[Tier] = None
    mode: Optional[str] = None
    attempt: int = Field(default=1, ge=1)
    role: str = "agent"
    # Guard 1
    input_texts: list[ScanText] = Field(default_factory=list)
    keep_terms: list[str] = Field(default_factory=list)
    # Guard 2
    tools: list[str] = Field(default_factory=list)
    # Guard 3
    budget: Optional[CaseBudget] = None
    reservation_id: Optional[str] = None
    # Guards 4 and 5
    outputs: list[BaseModel] = Field(default_factory=list)
    sources: list[AvailableSource] = Field(default_factory=list)
    grounding_numbers: Optional[frozenset[str]] = None
    # Guard 5 in H1 (state-change inputs)
    rules: Optional[RulesContext] = None

    @classmethod
    def from_job(cls, payload: JobPayload, **inputs: object) -> GuardContext:
        """Start a context from H1's ``agent.run`` payload; ``inputs`` fills
        the guard-specific fields (H2's usual entry point)."""
        return cls.model_validate(
            {
                "case_id": payload.case_id,
                "run_id": payload.run_id,
                "job_id": payload.job_id,
                "agent": payload.agent,
                "tier": payload.tier,
                "attempt": payload.attempt,
                **inputs,
            }
        )

    def source_index(self) -> dict[str, AvailableSource]:
        """``sources`` keyed by :func:`source_key` (last one wins)."""
        return {source.key: source for source in self.sources}
