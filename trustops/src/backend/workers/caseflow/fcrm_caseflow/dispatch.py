"""What happens, besides the state transition itself, when a given trigger
fires: which ``agent.run`` job(s) to publish (design A §3.1's "Action"
column, the "dispatch" half) and which gate (if any) to open (the "open
gate" half — Guard 7, design A §3.2).

Both tables are keyed on the :class:`~fcrm_caseflow.triggers.Trigger`
that just fired, not on ``TransitionResult.action``'s free-text label:
each "internal" dispatch trigger
(:mod:`fcrm_caseflow.triggers`'s module docstring) already identifies its
dispatch point uniquely — there is exactly one row in ``TRANSITIONS`` for
each of ``SCREENING_DISPATCH``/``ASSESS_DISPATCH``/``CRITIC_DISPATCH``/
``COMPLETENESS_DISPATCH``/``ALL_ITEMS_DECIDED`` — so switching on the
trigger is exactly as precise as switching on the action text would be,
without parsing a sentence meant for humans.

The fast-lane branch of ``ASSESS_DISPATCH`` (research+assessment in
parallel, or assessment alone) is **not** special-cased here: it reads
``flags.fast_lane`` directly, which :mod:`fcrm_caseflow.flags` already set
correctly by the time ``GATE_DECIDED_FAST_LANE_CONFIRM``'s transition
chains straight into it (both land in ``ASSESS`` in the same
:mod:`fcrm_caseflow.engine` call) — so the "confirmed: assessment only;
declined: full path" branch in that trigger's own action text falls out
of this table automatically rather than needing its own entry.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final, Optional

from fcrm_common.events import build_envelope
from fcrm_common.ids import deterministic_id, new_id
from fcrm_contracts.enums import AgentName, GateType, Role, Tier
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import (
    CaseDocumentStored,
    IngestRun,
    JobPayload,
    ObjectPrefix,
    ObjectRef,
)

from fcrm_caseflow.states import RunFlags
from fcrm_caseflow.store import CaseRun
from fcrm_caseflow.transitions import TransitionContext
from fcrm_caseflow.triggers import Trigger

#: Agent -> tier, duplicated (not imported) from ``fcrm_agents.spec
#: .REGISTRY`` by design: Caseflow "never reasons" and stays free of the
#: AI layer's own package (H1 does not depend on H2's ``fcrm_agents``), it
#: only needs the tier to size the budget reservation and populate
#: ``JobPayload.tier`` for the agent-worker/guard-3 budget check. Keep
#: this in step with ``fcrm_agents.spec.REGISTRY`` by hand if that table
#: ever changes.
AGENT_TIERS: Final[dict[AgentName, Tier]] = {
    AgentName.SCREENING: Tier.CHEAP,
    AgentName.INTAKE: Tier.CHEAP,
    AgentName.RESEARCH: Tier.STRONG,
    AgentName.ASSESSMENT: Tier.STRONG,
    AgentName.CRITIC: Tier.JUDGE,
    AgentName.NARRATIVE: Tier.MID,
    AgentName.EXAMINER: Tier.MID,
    AgentName.WATCHLIST: Tier.CHEAP,
    AgentName.POLICY_GAP: Tier.MID,
}

#: Guard 7's required role per gate type (gates-register.md).
REQUIRED_ROLE: Final[dict[GateType, Role]] = {
    GateType.PO_CONFIRM_CATEGORY: Role.PO,
    GateType.PO_CONFIRM_VALUE: Role.PO,
    GateType.PO_REUSE: Role.PO,
    GateType.PO_ANSWER_GAPS: Role.PO,
    GateType.FAST_LANE_CONFIRM: Role.ANALYST,
    GateType.REVIEW_ITEM: Role.ANALYST,
    GateType.ACK: Role.ANALYST,
    GateType.COMPLETENESS: Role.ANALYST,
    GateType.LOCK: Role.ANALYST,
    GateType.PACK_RELEASE: Role.ANALYST,
    GateType.VOTE: Role.COMMITTEE,
    GateType.REFLAG_ACK: Role.ANALYST,
    GateType.HANDOFF: Role.ANALYST,
    GateType.UNLOCK: Role.ANALYST,
}


@dataclass(frozen=True)
class AgentDispatch:
    """One ``agent.run`` to publish."""

    agent: AgentName
    scope: Optional[str] = None
    changed_fields: tuple[str, ...] = ()
    step_key: Optional[str] = None


@dataclass(frozen=True)
class DispatchPlan:
    """What :mod:`fcrm_caseflow.engine` should do after applying one
    trigger: zero or more agent jobs to publish, and the ``gates`` set
    those jobs' *own* dispatch is tracked under when more than one must
    all report done before the next trigger self-fires (the
    research+assessment / rerun joins).
    """

    agents: tuple[AgentDispatch, ...] = ()


def dispatch_for(
    trigger: Trigger,
    ctx: TransitionContext,
    flags: RunFlags,
    payload: Optional[dict[str, Any]] = None,
) -> DispatchPlan:
    """The jobs to dispatch immediately after ``trigger`` applied, given
    the run's flags *after* :func:`fcrm_caseflow.flags.next_flags` already
    ran for this same trigger (so ``flags.fast_lane`` is already correct
    for ``ASSESS_DISPATCH``'s branch). ``payload`` is the raw event
    payload that produced ``trigger`` (only ``CASE_ANSWERED`` reads it, for
    ``changed_field_ids`` — design A §4.3's ``case.answered`` payload —
    since that is not one of :class:`TransitionContext`'s own fields).
    """
    if trigger is Trigger.SCREENING_DISPATCH:
        return DispatchPlan((AgentDispatch(AgentName.SCREENING),))
    if trigger is Trigger.ASSESS_DISPATCH:
        if flags.fast_lane:
            return DispatchPlan((AgentDispatch(AgentName.ASSESSMENT),))
        return DispatchPlan(
            (AgentDispatch(AgentName.RESEARCH), AgentDispatch(AgentName.ASSESSMENT))
        )
    if trigger is Trigger.CRITIC_DISPATCH:
        return DispatchPlan((AgentDispatch(AgentName.CRITIC),))
    if trigger is Trigger.COMPLETENESS_DISPATCH:
        return DispatchPlan((AgentDispatch(AgentName.NARRATIVE, scope="completeness"),))
    if trigger is Trigger.CASE_ANSWERED:
        changed = tuple((payload or {}).get("changed_field_ids") or ())
        return DispatchPlan(
            (
                AgentDispatch(
                    AgentName.RESEARCH, scope="changed_fields", changed_fields=changed
                ),
                AgentDispatch(
                    AgentName.ASSESSMENT,
                    scope="changed_fields",
                    changed_fields=changed,
                ),
            )
        )
    if trigger is Trigger.ALL_ITEMS_DECIDED:
        return DispatchPlan(
            (AgentDispatch(AgentName.NARRATIVE, scope="recommendation"),)
        )
    if trigger is Trigger.GATE_DECIDED_PO_CONFIRM_CATEGORY_ALL and ctx.gaps_open:
        return DispatchPlan((AgentDispatch(AgentName.INTAKE, scope="questions_only"),))
    return DispatchPlan(())


#: Which gate (if any) opens as a direct consequence of ``trigger``
#: firing, beyond what ``_GATE_OPEN_CONDITIONAL`` decides from ``ctx``.
_GATE_OPEN_UNCONDITIONAL: Final[dict[Trigger, GateType]] = {
    Trigger.AGENT_DONE_NARRATIVE_COMPLETENESS: GateType.COMPLETENESS,
    Trigger.AGENT_FAILED_AFTER_RETRY: GateType.HANDOFF,
    Trigger.CASE_LOCKED: GateType.PACK_RELEASE,
    # No entry for AGENT_DONE_INTAKE / AGENT_DONE_ASSESS_BOTH: the old
    # one-combined-gate stand-ins (one po_confirm_category gate, one
    # review_item gate) are gone (ADR-28). The ``intake.publish`` and
    # ``assess.publish`` steps open one gate per proposed change type and per
    # clause / rating / control / critic finding, from their step result
    # (``StepResult.gates_to_open``), once S1 holds the items. Until a
    # handler is registered for those steps (``audit_map.STEP_FOR``) the two
    # triggers open no gate at all.
}


def gate_to_open(trigger: Trigger, ctx: TransitionContext) -> Optional[GateType]:
    """The gate type to open as a result of ``trigger`` having just
    applied, or ``None``.
    """
    direct = _GATE_OPEN_UNCONDITIONAL.get(trigger)
    if direct is not None:
        return direct
    if trigger is Trigger.AGENT_DONE_SCREENING and ctx.fast_lane_proposed:
        return GateType.FAST_LANE_CONFIRM
    if trigger is Trigger.GATE_DECIDED_REVIEW_ITEM and (
        ctx.band_changed or ctx.boundary_flagged
    ):
        return GateType.ACK
    return None


def build_job_envelope(
    dispatch: AgentDispatch,
    run: CaseRun,
    *,
    attempt: int = 1,
    refs: Optional[Mapping[str, Any]] = None,
) -> Envelope:
    """One ``agent.run`` envelope for ``dispatch`` against ``run``'s
    pinned versions (design A §3.1's "open run ... pins from active
    versions"). ``refs`` become ``JobPayload.refs``: the context-pack inputs
    H2 cannot derive from the job itself (:class:`fcrm_agents.context_pack
    .BuildRequest` fields such as ``domains`` or ``submission_version``).
    """
    payload = JobPayload(
        job_id=new_id("job"),
        case_id=run.snapshot.case_id,
        run_id=run.snapshot.run_id,
        agent=dispatch.agent,
        tier=AGENT_TIERS[dispatch.agent],
        step_key=dispatch.step_key or (dispatch.scope or dispatch.agent.value),
        attempt=attempt,
        scope=dispatch.scope,
        changed_fields=list(dispatch.changed_fields),
        pins=run.pins,
        refs=dict(refs or {}),
    )
    return build_envelope(
        "agent.run",
        payload.model_dump(mode="json"),
        case_id=run.snapshot.case_id,
        run_id=run.snapshot.run_id,
    )


# --- the pending-join keys of a dynamic set of jobs ---------------------------


def join_keys(jobs: Sequence[tuple[AgentName, str]]) -> list[str]:
    """What the run's pending-join set holds for ``jobs`` (``(agent, job_id)``
    pairs dispatched together): the agent's name when it appears once, the
    pair's own ``"<agent>:<job_id>"`` when the same agent runs several times
    (one intake job per document).

    Two intake jobs cannot both be tracked as ``"intake"``: the store removes
    every entry equal to the one that reported done, so the first
    ``agent.done`` would clear the join. :func:`done_join_keys` is the other
    half: the keys an ``agent.done`` may match.
    """
    names = [agent.value for agent, _ in jobs]
    return [
        name if names.count(name) == 1 else f"{name}:{job_id}"
        for name, (_, job_id) in zip(names, jobs, strict=True)
    ]


def done_join_keys(agent: AgentName, job_id: Optional[str]) -> tuple[str, ...]:
    """The pending-join keys an ``agent.done`` from ``agent`` / ``job_id`` may
    clear, most specific first (see :func:`join_keys`).
    """
    specific = (f"{agent.value}:{job_id}",) if job_id else ()
    return (*specific, agent.value)


def dispatch_refs(
    dispatch: AgentDispatch, run_refs: Mapping[str, Any]
) -> dict[str, Any]:
    """``JobPayload.refs`` for a job a *transition* dispatches (a step passes
    its own refs).

    The one such job that needs refs is the intake agent's ``questions_only``
    round, run after every change type is confirmed: H2's intake context
    builder reads one document window and refuses a job without ``doc_id``,
    so the job is given the first document the first round read
    (``intake_docs``, which ``intake.ingested`` records).
    """
    if dispatch.agent is AgentName.INTAKE and dispatch.scope == "questions_only":
        docs = run_refs.get("intake_docs")
        if isinstance(docs, list) and docs:
            return {"doc_id": str(docs[0])}
    return {}


# --- ingest.run: H1 -> H3 ------------------------------------------------------

#: Where H3 writes a case's canonical and anchored Markdown, inside the
#: canonical bucket (design A §7.1: ``canonical/{case_id}/{doc_id}.md``).
CANONICAL_KEY_ROOT: Final = "canonical"


@dataclass(frozen=True)
class IngestDispatch:
    """One ``ingest.run`` to publish: which stored document H3 should read."""

    doc_id: str
    bucket: str
    key: str
    keep_terms: tuple[str, ...] = ()


def ingest_for(
    trigger: Trigger, payload: Mapping[str, Any]
) -> Optional[IngestDispatch]:
    """The document to hand to H3 when ``trigger`` fired, or ``None``.

    Only ``CASE_DOCUMENT_STORED`` does: the document is read from where S1
    stored it (``bucket``/``key`` of the event), never copied through H1.

    Raises:
        pydantic.ValidationError: the ``case.document_stored`` payload does
            not carry the fields S1's contract promises (the message is
            retried, then parked).
    """
    if trigger is not Trigger.CASE_DOCUMENT_STORED:
        return None
    stored = CaseDocumentStored.model_validate(dict(payload))
    return IngestDispatch(
        doc_id=stored.doc_id,
        bucket=stored.bucket,
        key=stored.key,
        keep_terms=tuple(stored.keep_terms),
    )


def build_ingest_envelope(
    ingest: IngestDispatch, run: CaseRun, *, target_bucket: str
) -> Envelope:
    """The ``ingest.run`` envelope for ``ingest``, published on
    ``ingestion.jobs``. Its ``job_id`` is deterministic in the run and the
    document, so the same stored document always yields the same job.

    Args:
        ingest: The document to read.
        run: The case's run, for the case and run ids.
        target_bucket: The bucket H3 writes the canonical Markdown into; the
            key prefix is ``canonical/<case_id>/``.
    """
    case_id = run.snapshot.case_id
    payload = IngestRun(
        job_id=deterministic_id("job", run.snapshot.run_id, "ingest", ingest.doc_id),
        source=ObjectRef(bucket=ingest.bucket, key=ingest.key),
        target=ObjectPrefix(
            bucket=target_bucket, key_prefix=f"{CANONICAL_KEY_ROOT}/{case_id}/"
        ),
        purpose="case_document",
        doc_id=ingest.doc_id,
        guard1=True,
        keep_terms=list(ingest.keep_terms),
    )
    return build_envelope(
        "ingest.run",
        payload.model_dump(mode="json"),
        case_id=case_id,
        run_id=run.snapshot.run_id,
    )
