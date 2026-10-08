"""H1's intake core: the harness steps of scenes 2-7 (ADR-28).

Each handler is an ``async`` function ``(HarnessStep, StepDeps) -> StepResult``
(:mod:`fcrm_caseflow.steps`). It talks to S1, S3 and S4 through the injected
clients, writes the audit records of the walkthrough's vocabulary
(:mod:`fcrm_caseflow.cores.intake_records`), tells S1 what to show the PO, and
*describes* what should follow (gates to open, agent jobs to dispatch) in its
result; the engine applies that atomically. A handler never writes Mongo and
never publishes.

**Safe to run twice.** The broker redelivers a step whose handler failed, so
every handler is idempotent: S1 writes are PUT/PATCH of the full intended
state, audit records and S1 events carry an ``Idempotency-Key`` derived from
the step id, gates and ids are deterministic, and a handler that picks
something (the R5 analyst) first looks for what an earlier run already chose.

**What a step reads.** The trigger's own payload is ``step.args["payload"]``.
The merged proposals of the intake agents come from the results the engine
kept (:class:`~fcrm_caseflow.store.AgentResultStore`), never from the one
``agent.done`` that completed the join.

**Who sees what.** The PO sees ``visibility="po"`` and ``"all"`` events only,
and none of them carries a score, a band or a rating: this core never touches
one. Audit records carry counts, ids and source positions, never document
text beyond the quotes the agents were given (already Guard-1 masked).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Final, Optional

from fcrm_common.errors import ConflictError, NotFoundError
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.ids import deterministic_id
from fcrm_contracts.api.case_internal import (
    AssignAnalystRequest,
    CaseRecordInternal,
    DocumentPatch,
    DocumentStatus,
    FieldValueInternal,
    IntakePush,
    PrecedentMatch,
    ReusableField,
    StatePatch,
)
from fcrm_contracts.enums import AgentName, CaseState, ChangeType, GateType, StepKind
from fcrm_contracts.events.payloads import (
    CaseCreated,
    CaseDocumentStored,
    CaseSubmitted,
    CaseTypesChanged,
    GateDecided,
    HarnessStep,
    IngestDone,
    IngestFailed,
)

from fcrm_caseflow import routing
from fcrm_caseflow.clients.policy_client import PrecedentHit
from fcrm_caseflow.cores.assessment_scope import scope_refs
from fcrm_caseflow.cores.intake_merge import (
    CHANGE_TYPE_LABELS,
    MergedIntake,
    merge_intake,
)
from fcrm_caseflow.cores.intake_records import (
    change_type_label,
    plural,
    record_answer,
    record_category_decision,
    record_proposals,
    record_reuse_decision,
    record_value_decision,
)
from fcrm_caseflow.cores.refs import RERUN_PENDING_REF
from fcrm_caseflow.cores.shared import show as _show
from fcrm_caseflow.cores.shared import step_payload as _payload
from fcrm_caseflow.cores.shared import tokens_of
from fcrm_caseflow.dispatch import AgentDispatch
from fcrm_caseflow.steps import (
    AgentJobSpec,
    FollowUpStep,
    GateSpec,
    StepDeps,
    StepHandler,
    StepResult,
)
from fcrm_caseflow.store import CaseRun

logger = logging.getLogger(__name__)

#: Fields that say what this change *is*; a precedent's value for one of them
#: describes the precedent, not this case, so it is never offered for reuse.
#: (F01 owner, F02 description, F03 go-live date, F05 annual volume, F06 partner.)
CASE_IDENTITY_FIELDS: Final = frozenset({"F01", "F02", "F03", "F05", "F06"})

#: The extracted fields the precedent search is worded from ("what is this
#: change, where, with whom"): F02 description, F20 corridor, F06 partner.
_QUERY_FIELDS: Final = ("F02", "F20", "F06")

#: How many of the closest closed cases are looked at for one that holds values
#: the PO may reuse (the closest by words is not always the one with something
#: to offer).
PRECEDENT_CANDIDATES: Final = 5

#: What the PO is told when a file could not be used, by failure code. Anything
#: else gets the generic wording; the code itself stays in the audit record.
_PO_FAILURE_MESSAGES: Final[Mapping[str, str]] = {
    "scanned_pdf_no_ocr": "This file is a scan. Please upload a text version.",
}
_PO_FAILURE_DEFAULT: Final = (
    "This file could not be read. Please upload another version."
)

_MONTHS: Final = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)


# --- small helpers -------------------------------------------------------------


def _closed_month(closed_date: Optional[str]) -> str:
    """``"Jun 2026"`` from ``"2026-06-15"``; the date as given if it is not ISO."""
    if closed_date and len(closed_date) >= 7 and closed_date[4] == "-":
        try:
            month = int(closed_date[5:7])
            return f"{_MONTHS[month - 1]} {closed_date[:4]}"
        except (ValueError, IndexError):
            logger.warning("a precedent's closed date is not an ISO date")
    return closed_date or "unknown"


def _doc_name(record: CaseRecordInternal, doc_id: str) -> str:
    """The document's name, or its id when S1's record does not list it."""
    for doc in record.documents:
        if doc.doc_id == doc_id:
            return doc.name
    logger.warning("document is not in the case record", extra={"doc_id": doc_id})
    return doc_id


def _required_doc_id(doc_id: Optional[str], step: HarnessStep) -> str:
    if not doc_id:
        raise FcrmValidationError(
            f"the {step.step.value} payload names no document", step_id=step.step_id
        )
    return doc_id


# --- scene 2: the run opens ----------------------------------------------------


async def intake_open(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.created``: record the case and the run that opens for it (with the
    pinned versions), tell S1 the case is in intake and show it on the stream.
    """
    created = _payload(step, CaseCreated)
    case = deps.require_case()
    record = await case.get_record(step.case_id)
    await deps.append_record(
        step,
        "case.created",
        f"Case created by {record.owner.name}",
        {"title": record.title, "unit": record.unit},
        actor=created.actor,
    )
    await deps.append_record(
        step,
        "workflow.started",
        f"Intake core opened a run · field set v{record.fieldset_v} loaded",
        {
            "pins": deps.run.pins.model_dump(mode="json"),
            "trace_id": deps.run.trace_id,
        },
    )
    await case.patch_state(
        step.case_id, StatePatch(stage=CaseState.INTAKE, run_id=step.run_id)
    )
    await _show(
        deps,
        step,
        "case.stage_changed",
        "all",
        "Case moved to intake",
        {"stage": CaseState.INTAKE.value},
    )
    declared = created.data.change_types
    if not declared:
        return StepResult()
    # The manual path (ADR-72): the owner chose the types herself, so the
    # chain records them and the field set they resolve now, with no gate.
    names = ", ".join(CHANGE_TYPE_LABELS[t].lower() for t in declared)
    await deps.append_record(
        step,
        "case.types_changed",
        f"Owner chose {plural(len(declared), 'change type')}: {names}",
        {
            "added": [t.value for t in declared],
            "removed": [],
            "reason": None,
            "change_types": [t.value for t in declared],
            "intake_mode": created.data.intake_mode or "manual",
        },
        actor=created.actor,
    )
    return StepResult(refs_patch=await _resolve_fieldset(step, deps, record))


# --- scene 3: documents --------------------------------------------------------


async def intake_document_stored(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.document_stored``: record the upload. The ``ingest.run`` for H3 is
    published by the engine in the transaction that applied the trigger, not
    here.
    """
    stored = _payload(step, CaseDocumentStored)
    await deps.append_record(
        step,
        "document.stored",
        f"{stored.name} stored",
        {
            "doc_id": stored.doc_id,
            "name": stored.name,
            "sha256": stored.sha256,
            "size_bytes": stored.size_bytes,
            "content_type": stored.content_type,
            "raised_in_round": stored.raised_in_round,
        },
        actor=stored.actor,
    )
    return StepResult()


def _guard_summary(name: str, pages: Optional[int], guard1: Mapping[str, Any]) -> str:
    """The ``guard.input`` summary for a document that passed Guard 1."""
    status = str(guard1.get("status", "clean"))
    masked = guard1.get("pii_total", 0)
    masked_count = masked if isinstance(masked, int) else 0
    suffix = (
        f" · {plural(masked_count, 'personal-data field')} masked"
        if masked_count
        else ""
    )
    pages_text = f" ({plural(pages, 'page')})" if pages else ""
    return f"Guard 1 · {name}{pages_text} {status}{suffix}"


def _intake_jobs(run: CaseRun, record: CaseRecordInternal) -> tuple[AgentJobSpec, ...]:
    """One intake job per extracted document not yet read, once no document is
    still being extracted. Nothing while the run is not in INTAKE (a document
    added in a clarification round is the assessment core's business) or while
    any stored document is waiting for H3.
    """
    if run.state is not CaseState.INTAKE:
        return ()
    if any(doc.status == "stored" for doc in record.documents):
        return ()
    dispatched = {str(d) for d in run.refs.get("intake_docs") or ()}
    waiting = sorted(
        doc.doc_id
        for doc in record.documents
        if doc.status == "extracted" and doc.doc_id not in dispatched
    )
    return tuple(
        AgentJobSpec(
            AgentDispatch(AgentName.INTAKE, scope="full", step_key=f"intake:{doc_id}"),
            {"doc_id": doc_id},
        )
        for doc_id in waiting
    )


def _dispatch_result(run: CaseRun, jobs: Sequence[AgentJobSpec]) -> StepResult:
    """The step result that dispatches ``jobs`` and remembers which documents
    they read (``intake_docs``, in dispatch order, first round first).
    """
    if not jobs:
        return StepResult()
    previous = [str(d) for d in run.refs.get("intake_docs") or ()]
    docs = [str(job.refs["doc_id"]) for job in jobs]
    return StepResult(
        agent_jobs_to_dispatch=tuple(jobs),
        refs_patch={"intake_docs": [*previous, *docs]},
        counter_deltas={"agents_run": len(jobs)},
    )


def _after_ingest(deps: StepDeps, record: CaseRecordInternal) -> StepResult:
    """What follows a document being extracted (or refused). At intake: one intake
    agent per document, once none is still being read. In a clarification round a
    document the owner added with an answer is not read by the intake agent; if
    that answer is waiting for it, the assessment core's ``rerun.record`` step
    runs again to dispatch the re-run. The same holds in review for a document
    the owner added with an ad-hoc answer whose re-run waits for it (ADR-73); at
    any other stage the document is recorded and extracted, and nothing follows.
    """
    if deps.run.state in (CaseState.CLARIFICATION, CaseState.REVIEW):
        if deps.run.refs.get(RERUN_PENDING_REF):
            return StepResult(
                follow_up_steps=(
                    FollowUpStep(StepKind.RERUN_RECORD, {"trigger": "follow_up"}),
                )
            )
        return StepResult()
    return _dispatch_result(deps.run, _intake_jobs(deps.run, record))


async def intake_ingested(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``ingest.done``: record what H3 produced on the document, what Guard 1
    did to it, tell the PO it was read, and, once every document of the case is
    extracted, dispatch one intake agent per document.
    """
    done = _payload(step, IngestDone)
    doc_id = _required_doc_id(done.doc_id, step)
    case = deps.require_case()
    await case.patch_document(
        doc_id,
        DocumentPatch(
            status="extracted",
            canonical_key=done.canonical_key,
            anchored_key=done.anchored_key,
            pages=done.pages,
            page_map=done.page_map,
            guard1=done.guard1,
        ),
    )
    record = await case.get_record(step.case_id)
    name = _doc_name(record, doc_id)
    audit = await deps.append_record(
        step,
        "guard.input",
        _guard_summary(name, done.pages, done.guard1),
        {
            "doc_id": doc_id,
            "pages": done.pages,
            "guard1": done.guard1,
            "warnings": done.warnings,
        },
        actor="system",
    )
    await _show(
        deps,
        step,
        "document.extracted",
        "po",
        f"{name} was read ({plural(done.pages, 'page')})",
        {"doc_id": doc_id, "name": name, "pages": done.pages, "status": "extracted"},
        record_seq=audit.seq,
    )
    return _after_ingest(deps, record)


async def intake_ingest_failed(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``ingest.failed``: mark the document quarantined (Guard 1 blocked its
    text) or failed, record why, tell the PO to upload another version. No agent
    ever reads that document; the case's other documents are still read once
    nothing is waiting for H3.
    """
    failed = _payload(step, IngestFailed)
    doc_id = _required_doc_id(failed.doc_id, step)
    case = deps.require_case()
    status: DocumentStatus = "quarantined" if failed.quarantined else "failed"
    await case.patch_document(
        doc_id, DocumentPatch(status=status, guard1=failed.guard1 or None)
    )
    record = await case.get_record(step.case_id)
    name = _doc_name(record, doc_id)
    what = (
        "Guard 1 blocked the text; no agent reads it"
        if failed.quarantined
        else f"could not be read ({failed.code})"
    )
    audit = await deps.append_record(
        step,
        "guard.input",
        f"Guard 1 · {name} {status}: {what}",
        {
            "doc_id": doc_id,
            "code": failed.code,
            "quarantined": failed.quarantined,
            "guard1": failed.guard1,
        },
        actor="system",
    )
    await _show(
        deps,
        step,
        "document.extracted",
        "po",
        f"{name} could not be used",
        {
            "doc_id": doc_id,
            "name": name,
            "status": status,
            "message": _PO_FAILURE_MESSAGES.get(failed.code, _PO_FAILURE_DEFAULT),
        },
        record_seq=audit.seq,
    )
    return _after_ingest(deps, record)


# --- scene 3-6: the agents' proposals reach the PO ----------------------------


def _precedent_query(merged: MergedIntake, record: CaseRecordInternal) -> str:
    """The words the precedent search runs on: the extracted description,
    corridor and partner, then the proposed change types; the case title when
    the agents extracted none of them.
    """
    by_field = {v.field_id: v.value for v in merged.values}
    parts = [by_field[f] for f in _QUERY_FIELDS if f in by_field]
    parts.extend(c.label for c in merged.categories)
    return " ".join(parts) if parts else record.title


def _not_reusable_reason(
    field: FieldValueInternal, extracted: frozenset[str]
) -> Optional[str]:
    if field.field_id in CASE_IDENTITY_FIELDS:
        return "describes the other case, not this one"
    if field.field_id in extracted:
        return "this case's documents already give a value"
    return None


def _reusable_fields(
    precedent: CaseRecordInternal, extracted: frozenset[str]
) -> list[ReusableField]:
    """What the PO may reuse from ``precedent``: every value it holds, marked
    reusable unless it describes the precedent itself (:data:`CASE_IDENTITY_FIELDS`)
    or this case's documents already gave the field a value.
    """
    fields: list[ReusableField] = []
    for field in sorted(precedent.fields, key=lambda f: f.field_id):
        if not field.value:
            continue
        reason = _not_reusable_reason(field, extracted)
        fields.append(
            ReusableField(
                field_id=field.field_id,
                label=field.label,
                value=field.value,
                reusable=reason is None,
                reason=reason,
            )
        )
    return fields


@dataclass(frozen=True)
class _Candidate:
    """A closed case S3 found, S1's record of it and what it offers for reuse."""

    hit: PrecedentHit
    record: CaseRecordInternal
    fields: list[ReusableField]

    @property
    def offers_values(self) -> bool:
        """Whether the PO may reuse at least one of its values."""
        return any(f.reusable for f in self.fields)


async def _closest_with_values(
    deps: StepDeps,
    record: CaseRecordInternal,
    merged: MergedIntake,
    extracted: frozenset[str],
) -> Optional[_Candidate]:
    """The closest closed case S1 knows that holds something the PO may reuse,
    else the closest one S1 knows, else ``None``.

    S3 ranks by similarity of the words; a closed case with nothing to offer
    (no value of its own that this case's documents do not already give) is a
    precedent the PO cannot use, so the first of the closest
    :data:`PRECEDENT_CANDIDATES` that has reusable fields wins.
    """
    hits = await deps.require_policy().closest_precedents(
        _precedent_query(merged, record), PRECEDENT_CANDIDATES
    )
    fallback: Optional[_Candidate] = None
    for hit in hits:
        if hit.case_id == record.case_id:
            continue
        try:
            found = await deps.require_case().get_record(hit.case_id)
        except NotFoundError:
            logger.warning(
                "the precedent S3 found is not a case S1 knows",
                extra={"precedent_case_id": hit.case_id},
            )
            continue
        candidate = _Candidate(hit, found, _reusable_fields(found, extracted))
        if candidate.offers_values:
            return candidate
        fallback = fallback or candidate
    return fallback


async def _precedent(
    deps: StepDeps, record: CaseRecordInternal, merged: MergedIntake
) -> Optional[PrecedentMatch]:
    """The closest closed case with values to reuse, and what may be reused
    from it, or ``None``.

    The first publish asks S3 (``precedent_cases``, the closest few) and
    remembers the case id, similarity and closing month in the run's refs; later
    publishes reuse that match instead of searching again, so what the PO is
    shown (and may already have accepted) cannot change under them. A match S1
    does not know is left out, logged.
    """
    refs = deps.run.refs
    extracted = frozenset(v.field_id for v in merged.values)
    case_id = refs.get("precedent_case_id")
    similarity = refs.get("precedent_similarity")
    closed_month = refs.get("precedent_closed_month")
    if (
        isinstance(case_id, str)
        and isinstance(similarity, float)
        and isinstance(closed_month, str)
    ):
        try:
            found = await deps.require_case().get_record(case_id)
        except NotFoundError:
            logger.warning(
                "the precedent S3 found is not a case S1 knows",
                extra={"precedent_case_id": case_id},
            )
            return None
        fields = _reusable_fields(found, extracted)
    else:
        chosen = await _closest_with_values(deps, record, merged, extracted)
        if chosen is None:
            return None
        found, fields = chosen.record, chosen.fields
        similarity = chosen.hit.similarity
        closed_month = _closed_month(chosen.hit.closed_date)
    return PrecedentMatch(
        case_id=found.case_id,
        title=found.title,
        unit=found.unit or "",
        closed_month=closed_month,
        similarity=similarity,
        fields=fields,
    )


def _gates_for(
    merged: MergedIntake, precedent: Optional[PrecedentMatch]
) -> tuple[GateSpec, ...]:
    """The PO's gates for what was just pushed to S1.

    One ``po_confirm_category`` gate per proposed change type and one
    ``po_answer_gaps`` gate per blocking question (a non-blocking question is
    shown, never required): their ids are the ones pushed on each item. Then,
    as the gates register has it ("per item, never bulk"), one
    ``po_confirm_value`` gate per proposed value and one ``po_reuse`` gate for
    the precedent. Neither moves the case; they exist so the PO's confirm,
    correct or reuse decision (which S1 publishes as ``gate.decided``) meets a
    gate H1 opened (Guard 7) instead of being refused as unknown. The id of
    any of them is ``deterministic_id("gate", run_id, <gate type>, <subject
    key>)`` with the field id (or the precedent's case id) as subject key.
    """
    categories = tuple(
        GateSpec(
            GateType.PO_CONFIRM_CATEGORY,
            c.change_type.value,
            {"kind": "change_type", "id": c.change_type.value},
        )
        for c in merged.categories
    )
    gaps = tuple(
        GateSpec(
            GateType.PO_ANSWER_GAPS,
            g.question_id,
            {"kind": "gap", "id": g.question_id},
        )
        for g in merged.gaps
        if g.blocking
    )
    values = tuple(
        GateSpec(
            GateType.PO_CONFIRM_VALUE, v.field_id, {"kind": "field", "id": v.field_id}
        )
        for v in merged.values
    )
    reuse = (
        (
            GateSpec(
                GateType.PO_REUSE,
                precedent.case_id,
                {"kind": "precedent", "id": precedent.case_id},
            ),
        )
        if precedent is not None
        else ()
    )
    return (*categories, *gaps, *values, *reuse)


def _owner_types(
    record: CaseRecordInternal, refs: Mapping[str, Any]
) -> frozenset[ChangeType]:
    """The change types the owner put on the case herself before the
    assistant proposed them (ADR-72): held by S1 but never pushed as a
    proposal. Such a type gets no ``po_confirm_category`` gate and no second
    item: the owner already decided it, and a gate nobody can decide would
    keep ``fieldset.resolved`` from ever firing. A type pushed in an earlier
    round stays a proposal, however the owner decided it since (a run that
    published before ``intake_types_pushed`` existed pushed every proposal).
    """
    previous = refs.get("intake_types_pushed")
    if previous is None:
        previous = refs.get("intake_types_proposed")
    pushed = {str(t) for t in previous or ()}
    return frozenset(t for t in record.change_types if t.value not in pushed)


async def intake_publish(step: HarnessStep, deps: StepDeps) -> StepResult:
    """Every intake job of the round is done: merge their proposals (one
    candidate per field, the most confident), look for a precedent, push it all
    to S1 in ONE idempotent write, write the audit records, and open the PO's
    gates: one per proposed change type, one per blocking question.

    A second pass (the ``questions_only`` round after the change types are
    confirmed) merges *every* intake result of the run again and pushes the
    whole set; the records and the precedent are written once.

    Raises:
        fcrm_common.errors.NotFoundError: no intake result is stored for the
            run (a wiring fault: the engine stores each ``agent.done``).
    """
    case = deps.require_case()
    results = await deps.require_agent_results().list_for(step.run_id, AgentName.INTAKE)
    if not results:
        raise NotFoundError(f"no intake agent result is stored for run {step.run_id}")
    record = await case.get_record(step.case_id)
    labels = {f.field_id: f.label for f in record.fields}
    merged = merge_intake(results, run_id=step.run_id, labels=labels)
    published = {str(j) for j in deps.run.refs.get("intake_published_jobs") or ()}
    new_results = [r for r in results if r.job_id not in published]
    fresh = merge_intake(new_results, run_id=step.run_id, labels=labels)
    owners = _owner_types(record, deps.run.refs)
    categories = [c for c in merged.categories if c.change_type not in owners]
    pushable = replace(merged, categories=tuple(categories))

    precedent = await _precedent(deps, record, merged)
    await case.put_intake(
        step.case_id,
        IntakePush(
            run_id=step.run_id,
            candidates_found=merged.candidates_found,
            values=list(merged.values),
            categories=categories,
            gaps=list(merged.gaps),
            precedent=precedent,
            flags=list(merged.flags),
        ),
    )
    await record_proposals(
        step, deps, fresh, first_publish=not published, precedent=precedent
    )
    await _show(
        deps,
        step,
        "intake.proposals_ready",
        "po",
        f"{plural(len(merged.values), 'value')} and "
        f"{plural(len(merged.categories), 'change type')} are ready to review",
        {
            "values": len(merged.values),
            "change_types": len(merged.categories),
            "questions": len(merged.gaps),
            "precedent_case_id": precedent.case_id if precedent else None,
        },
        ordinal=len(published),
    )
    refs: dict[str, Any] = {
        "intake_published_jobs": sorted(r.job_id for r in results),
        "intake_doc_ids": list(merged.doc_ids),
        "intake_types_proposed": [c.change_type.value for c in merged.categories],
        "intake_types_pushed": [c.change_type.value for c in categories],
        "intake_values_proposed": len(merged.values),
    }
    if precedent is not None:
        refs["precedent_case_id"] = precedent.case_id
        refs["precedent_similarity"] = precedent.similarity
        refs["precedent_closed_month"] = precedent.closed_month
    return StepResult(
        gates_to_open=_gates_for(pushable, precedent),
        refs_patch=refs,
        # The intake agents' tokens count against the case's 120k like every
        # other agent's: the first live run's monitor left them out (a replayed
        # publish has no new results, so adds nothing).
        counter_deltas={"tokens_used": tokens_of(new_results)},
    )


# --- scene 4-7: what the PO decides --------------------------------------------


async def intake_fieldset_resolved(step: HarnessStep, deps: StepDeps) -> StepResult:
    """The last open change type was decided: record that decision and the
    field set it resolved (the union of the confirmed types' fields, which S1
    holds), and show the PO the required set.
    """
    decided = _payload(step, GateDecided)
    record = await deps.require_case().get_record(step.case_id)
    await record_category_decision(step, deps, decided)
    return StepResult(refs_patch=await _resolve_fieldset(step, deps, record))


async def _resolve_fieldset(
    step: HarnessStep, deps: StepDeps, record: CaseRecordInternal
) -> dict[str, Any]:
    """Record ``fieldset.resolved`` for the types S1 holds on ``record`` (the
    union of their fields), show the owner the required count and return the
    refs that remember it.
    """
    required = len(record.required_field_ids)
    types = len(record.change_types)
    audit = await deps.append_record(
        step,
        "fieldset.resolved",
        f"Required field set = union of {plural(types, 'type')} = "
        f"{plural(required, 'field')}",
        {
            "change_types": [t.value for t in record.change_types],
            "required_field_ids": list(record.required_field_ids),
            "fieldset_v": record.fieldset_v,
        },
        actor="system",
    )
    await _show(
        deps,
        step,
        "fieldset.resolved",
        "po",
        f"{plural(required, 'field')} are required for your change",
        {"required_fields": required, "change_types": types},
        record_seq=audit.seq,
    )
    return {"fieldset_resolved": True, "required_fields": required}


async def intake_types_changed(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.types_changed`` (ADR-72): the owner added or removed a change
    type with no open gate to carry it. Record who did what (with the reason
    for a removal) and the field set S1 now holds. Nothing moves and no agent
    runs: a type the owner chose herself needs no confirming.
    """
    changed = _payload(step, CaseTypesChanged)
    record = await deps.require_case().get_record(step.case_id)
    label = change_type_label(changed.change_type.value)
    reason = f" · {changed.reason}" if changed.reason else ""
    await deps.append_record(
        step,
        "case.types_changed",
        f"Owner {changed.action} change type: {label}{reason}",
        {
            "added": [changed.change_type.value] if changed.action == "added" else [],
            "removed": (
                [changed.change_type.value] if changed.action == "removed" else []
            ),
            "reason": changed.reason,
            "change_types": [t.value for t in changed.change_types],
        },
        actor=changed.actor,
    )
    return StepResult(refs_patch=await _resolve_fieldset(step, deps, record))


async def intake_po_action(step: HarnessStep, deps: StepDeps) -> StepResult:
    """A PO decision that moves nothing: record it as the walkthrough does (a
    change type confirmed while others are open, a value corrected, values
    reused, a question answered). If gaps remain after the last change type,
    the transition that decided it has already dispatched the intake agent for
    questions only.

    Raises:
        fcrm_common.errors.ValidationError: the gate type is not one the PO
            decides during intake.
    """
    decided = _payload(step, GateDecided)
    gate_type = decided.gate_type
    if gate_type is GateType.PO_CONFIRM_CATEGORY:
        await record_category_decision(step, deps, decided)
    elif gate_type is GateType.PO_CONFIRM_VALUE:
        await record_value_decision(step, deps, decided)
    elif gate_type is GateType.PO_REUSE:
        await record_reuse_decision(step, deps, decided)
    elif gate_type is GateType.PO_ANSWER_GAPS:
        await record_answer(step, deps, decided)
    else:
        raise FcrmValidationError(
            f"intake.po_action does not handle a {gate_type.value} decision",
            step_id=step.step_id,
        )
    return StepResult()


# --- scene 7: submit and triage ------------------------------------------------


async def submit_record(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``case.submitted`` (S1 re-checked the four submit checks; the transition
    guard re-reads their verdict): record the validation and the submission.
    """
    submitted = _payload(step, CaseSubmitted)
    passed = sum(1 for check in submitted.checks if check.ok)
    await deps.append_record(
        step,
        "gate.validation",
        f"{passed} of {len(submitted.checks)} submission checks passed",
        {"checks": [c.model_dump(mode="json") for c in submitted.checks]},
        actor="system",
    )
    await deps.append_record(
        step,
        "case.submitted",
        f"Submission v{submitted.submission_version} · "
        f"{submitted.filled} of {submitted.required} fields",
        {
            "submission_version": submitted.submission_version,
            "filled": submitted.filled,
            "required": submitted.required,
            "change_types": [t.value for t in submitted.change_types],
        },
        actor=submitted.actor,
    )
    return StepResult()


def _intake_grade(
    record: CaseRecordInternal, refs: Mapping[str, Any]
) -> dict[str, int]:
    """The intake agent's grade, as counts only: of the values it proposed,
    how many the PO left as extracted and how many she corrected; of the change
    types it proposed, how many she confirmed.
    """
    kept = sum(1 for f in record.fields if f.origin == "extracted")
    corrected = sum(
        1
        for f in record.fields
        if f.origin == "owner_declared" and f.original_value is not None
    )
    types_proposed = [str(t) for t in refs.get("intake_types_proposed") or ()]
    return {
        "values_proposed": int(refs.get("intake_values_proposed") or 0),
        "values_kept": kept,
        "values_corrected": corrected,
        "types_proposed": len(types_proposed),
        "types_confirmed": sum(
            1 for t in record.change_types if t.value in types_proposed
        ),
    }


def _pick_analyst(
    record: CaseRecordInternal, open_counts: Mapping[str, int]
) -> tuple[str, bool]:
    """``(analyst_id, already_assigned)``. A case S1 already assigned (an
    earlier run of this step) keeps its analyst: a rerun must not pick again,
    because the open counts now include this very case.

    Raises:
        fcrm_common.errors.ConflictError: R5 has no analyst to pick from.
    """
    if record.analyst is not None:
        return record.analyst.id, True
    try:
        return routing.pick_auto_triage_analyst(dict(open_counts)), False
    except ValueError as exc:
        raise ConflictError("R5 auto-triage found no analyst to assign") from exc


async def triage_assign(step: HarnessStep, deps: StepDeps) -> StepResult:
    """R5: assign the new case to the analyst with the fewest open cases (ties
    alphabetically by id), record it with the intake eval's counts, move S1's
    stage to screening and tell the PO who has the case (the name only).
    """
    case = deps.require_case()
    before = await case.get_record(step.case_id)
    load = await case.analyst_load()
    open_counts = {item.analyst_id: item.open_case_count for item in load.loads}
    analyst_id, assigned = _pick_analyst(before, open_counts)
    if not assigned:
        await case.assign(
            step.case_id,
            AssignAnalystRequest(
                analyst_id=analyst_id, reason="R5 lowest load", open_counts=open_counts
            ),
            idempotency_key=deterministic_id("idem", step.step_id, "assign"),
        )
    record = await case.get_record(step.case_id)
    name = record.analyst.name if record.analyst else analyst_id
    count = open_counts.get(analyst_id)
    load_text = (
        f" ({plural(count, 'open case')}, lowest load)" if count is not None else ""
    )
    audit = await deps.append_record(
        step,
        "triage.assigned",
        f"Assigned to {name}{load_text}",
        {"analyst_id": analyst_id, "open_counts": open_counts, "rule": "R5"},
        actor="system",
    )
    grade = _intake_grade(record, deps.run.refs)
    await deps.append_record(
        step,
        "eval.recorded",
        f"Intake eval: {grade['values_kept']} of {grade['values_proposed']} values "
        f"kept · {grade['values_corrected']} corrected · "
        f"{grade['types_confirmed']} of {grade['types_proposed']} types confirmed",
        grade,
        actor="system",
    )
    await case.patch_state(
        step.case_id, StatePatch(stage=CaseState.SCREENING, run_id=step.run_id)
    )
    await _show(
        deps,
        step,
        "case.assigned",
        "po",
        f"Your case was assigned to {name}",
        {"analyst_name": name},
        record_seq=audit.seq,
    )
    # What the engine hands every agent job it dispatches from here on (the
    # screening job is the first): the domains in scope and the submission the
    # agents read. Nothing about the case beyond ids and names.
    return StepResult(refs_patch=scope_refs(record))


#: The intake core's handlers by step kind (``fcrm_caseflow.cores.default_handlers``).
HANDLERS: Final[Mapping[StepKind, StepHandler]] = {
    StepKind.INTAKE_OPEN: intake_open,
    StepKind.INTAKE_DOCUMENT_STORED: intake_document_stored,
    StepKind.INTAKE_INGESTED: intake_ingested,
    StepKind.INTAKE_INGEST_FAILED: intake_ingest_failed,
    StepKind.INTAKE_PUBLISH: intake_publish,
    StepKind.INTAKE_FIELDSET_RESOLVED: intake_fieldset_resolved,
    StepKind.INTAKE_PO_ACTION: intake_po_action,
    StepKind.INTAKE_TYPES_CHANGED: intake_types_changed,
    StepKind.SUBMIT_RECORD: submit_record,
    StepKind.TRIAGE_ASSIGN: triage_assign,
}
