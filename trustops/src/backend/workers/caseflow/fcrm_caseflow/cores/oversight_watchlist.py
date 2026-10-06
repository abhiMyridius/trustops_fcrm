"""H1's watchlist half of the oversight core: scene 20's jurisdiction fixture,
the re-flags and what an analyst does with them (ADR-28).

``watchlist.publish`` has two ends, on a harness-level run that belongs to the
list version (:mod:`fcrm_caseflow.oversight_runs`):

* ``jurisdiction.list_published`` (S3 activated a list): record the publication
  and ask S1 which open cases the added countries touch. **Code decides who is
  affected**, a set intersection in S1; with no candidate nothing more happens
  and no model is called. Otherwise one ``agent.run watchlist`` is dispatched to
  write the per-case explanation.
* ``agent.done watchlist`` (or ``agent.failed``): the agent's proposals are held
  to what S1 holds. A proposal for a case that is not a candidate, or for a
  removal, is dropped. **A candidate is never dropped**: one the agent did not
  explain, and every candidate when the agent failed, is re-flagged with a plain
  reason code wrote (``proposed_by: code``), so an affected case cannot be missed
  because a model was. The re-flags are held by S1, ``reflag.proposed`` goes on
  each case's own chain, and one ``reflag_ack`` gate per re-flag opens for an
  analyst.

A re-flag never changes a score, a stage or a lock, and never reopens a case: it
asks a human to look, and the human decides (ADR-07).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from itertools import batched
from typing import Any, Final, Optional

from fcrm_common.errors import ConflictError
from fcrm_common.ids import deterministic_id
from fcrm_contracts.api.case_internal_reflags import (
    MAX_REFLAGS_PER_POST,
    REASON_MAX_CHARS,
    ListEntryRef,
    ReflagCandidate,
    ReflagIn,
    ReflagsPost,
    RerunAgent,
)
from fcrm_contracts.enums import AgentName, GateType, StepKind
from fcrm_contracts.events.payloads import HarnessStep, JurisdictionListPublished
from fcrm_contracts.proposals.provenance import ListSourceRef
from fcrm_contracts.proposals.watchlist import ReflagProposal
from pydantic import ValidationError

from fcrm_caseflow.cores.intake_records import plural, short
from fcrm_caseflow.cores.shared import idem, post_proposal, step_payload, tokens_of
from fcrm_caseflow.dispatch import AgentDispatch
from fcrm_caseflow.gates import deterministic_gate_id
from fcrm_caseflow.steps import (
    AgentJobSpec,
    GateSpec,
    StepDeps,
    StepHandler,
    StepResult,
    UnknownStepKindError,
)
from fcrm_caseflow.triggers import Trigger

logger = logging.getLogger(__name__)

#: The ``CaseRun.refs`` key holding this round's facts: ``list_to``, ``added``,
#: ``fixture``, and after publishing ``reflags``, ``dropped``, ``failed``.
WATCHLIST_REF: Final = "watchlist"
#: The list the jurisdiction clauses and the fixture are filed under.
LIST_ID: Final = "FATF"
SYNTHETIC_NOTE: Final = " (a synthetic test list)"


def reflag_id_for(version: str, case_id: str, jurisdiction: str) -> str:
    """The id of a re-flag: the same list version, case and jurisdiction always
    give the same one, so a second post creates nothing.
    """
    return deterministic_id("rf", version, case_id, jurisdiction)


def code_reason(version: str, jurisdiction: str, fixture: bool) -> str:
    """The reason H1 writes itself for a candidate no agent explained. It states
    only what code knows: the country, the list version and that the case names
    the country.
    """
    note = SYNTHETIC_NOTE if fixture else ""
    return (
        f"{jurisdiction} was added to jurisdiction list {version}{note}; this case "
        f"names {jurisdiction}, and its research ran before that change."
    )


def _list_entry(proposal: ReflagProposal, version: str) -> ListEntryRef:
    """The list entry a proposal cites: the agent's own when it cited the list,
    else the list version alone.
    """
    ref = proposal.source_ref
    list_id = ref.list_id if isinstance(ref, ListSourceRef) else LIST_ID
    return ListEntryRef(
        list_id=list_id[:64], version=version, quote=proposal.quote[:400] or None
    )


def usable_proposals(
    accepted: Sequence[Mapping[str, Any]],
    candidates: Sequence[ReflagCandidate],
    version: str,
) -> tuple[dict[tuple[str, str], ReflagProposal], int]:
    """The agent's proposals that stand, by ``(case, jurisdiction)``, and how many
    were dropped.

    A proposal stands only if S1's candidate set has that case and country, the
    change is an addition and it is for this list version; the first proposal
    for a pair wins. Anything else is dropped (counted, never held).
    """
    wanted = {(c.case_id, c.jurisdiction) for c in candidates}
    kept: dict[tuple[str, str], ReflagProposal] = {}
    dropped = 0
    for raw in accepted:
        try:
            proposal = ReflagProposal.model_validate(raw)
        except ValidationError:
            dropped += 1
            continue
        key = (proposal.case_id, proposal.jurisdiction)
        stands = (
            key in wanted
            and key not in kept
            and proposal.list_change == "added"
            and proposal.list_version == version
            and 10 <= len(proposal.explanation.strip()) <= REASON_MAX_CHARS
        )
        if stands:
            kept[key] = proposal
        else:
            dropped += 1
    return kept, dropped


def build_reflags(
    step: HarnessStep,
    candidates: Sequence[ReflagCandidate],
    proposals: Mapping[tuple[str, str], ReflagProposal],
    *,
    version: str,
    list_from: Optional[str],
    fixture: bool,
    proposal_id: Optional[str],
) -> list[ReflagIn]:
    """One re-flag per candidate: the agent's explanation when it gave one that
    stands, otherwise the reason code writes.
    """
    rows: list[ReflagIn] = []
    for candidate in candidates:
        proposal = proposals.get((candidate.case_id, candidate.jurisdiction))
        reflag_id = reflag_id_for(version, candidate.case_id, candidate.jurisdiction)
        if proposal is not None:
            reason = proposal.explanation.strip()
            entry = _list_entry(proposal, version)
            rerun: list[RerunAgent] = (
                ["research", "assessment"] if proposal.rerun else ["research"]
            )
        else:
            reason = code_reason(version, candidate.jurisdiction, fixture)
            entry = ListEntryRef(list_id=LIST_ID, version=version)
            rerun = ["research"]
        rows.append(
            ReflagIn(
                reflag_id=reflag_id,
                case_id=candidate.case_id,
                jurisdiction=candidate.jurisdiction,
                list_version=version,
                list_from=list_from,
                list_change="added",
                fixture=fixture,
                reason=reason,
                proposed_by="agent" if proposal is not None else "code",
                rerun=rerun,
                list_entry=entry,
                proposal_id=proposal_id if proposal is not None else None,
                run_id=step.run_id,
                gate_id=deterministic_gate_id(
                    step.run_id, GateType.REFLAG_ACK, reflag_id
                ),
            )
        )
    return rows


async def _previous_list(deps: StepDeps, version: str) -> str:
    """The list ``version`` replaced: the newest *real* list S3 published before
    it (a synthetic fixture is never anyone's predecessor, and a fixture's own
    ``published`` is the word ``fixture``, not a date, so for a fixture the
    newest real list is the one it was derived from).

    When S3 knows no earlier version the list is its own predecessor (nothing to
    diff; the code candidates still stand) and that is logged.
    """
    versions = await deps.require_policy().jurisdiction_versions()
    target = next((v for v in versions if v.version == version), None)
    if target is None:
        raise ConflictError(
            f"S3 holds no jurisdiction list {version}", list_version=version
        )
    earlier = [
        v
        for v in versions
        if v.version != version
        and not v.fixture
        and (target.fixture or v.published <= target.published)
    ]
    if not earlier:
        logger.warning(
            "no earlier jurisdiction list to diff against",
            extra={"case_id": deps.run.snapshot.case_id, "list_version": version},
        )
        return version
    return max(earlier, key=lambda v: (v.published, v.version)).version


async def _open(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``jurisdiction.list_published``: record it, find the candidates, and ask
    the watchlist agent to explain them (only when there are any).
    """
    event = step_payload(step, JurisdictionListPublished)
    await deps.append_record(
        step,
        "jurisdiction.published",
        f"Jurisdiction list {event.version} published"
        f" · {len(event.added)} added, {len(event.removed)} removed",
        {
            "version": event.version,
            "added": event.added,
            "removed": event.removed,
            "fixture": event.fixture,
        },
    )
    facts: dict[str, Any] = {
        "list_to": event.version,
        "added": list(event.added),
        "fixture": event.fixture,
    }
    candidates = (
        await deps.require_oversight().reflags.candidates(event.added)
        if event.added
        else []
    )
    if not candidates:
        logger.info(
            "no open case is touched by the new list",
            extra={"case_id": step.case_id, "list_version": event.version},
        )
        return StepResult(refs_patch={WATCHLIST_REF: {**facts, "candidates": 0}})
    list_from = await _previous_list(deps, event.version)
    job_id = deterministic_id("job", step.run_id, "watchlist", event.version)
    return StepResult(
        agent_jobs_to_dispatch=(
            AgentJobSpec(
                dispatch=AgentDispatch(
                    AgentName.WATCHLIST,
                    step_key=f"watchlist:{event.version}",
                    job_id=job_id,
                ),
                refs={"list_from": list_from, "list_to": event.version},
                pins=deps.run.pins.model_copy(update={"jurisdictions_v": list_from}),
            ),
        ),
        refs_patch={
            WATCHLIST_REF: {
                **facts,
                "list_from": list_from,
                "candidates": len(candidates),
                "job_id": job_id,
            }
        },
    )


def _round_facts(step: HarnessStep, deps: StepDeps) -> Mapping[str, Any]:
    """This round's facts from the run's refs.

    Raises:
        fcrm_common.errors.ConflictError: The run holds none (a result for a
            round that never opened).
    """
    facts = deps.run.refs.get(WATCHLIST_REF)
    if not isinstance(facts, Mapping) or "list_to" not in facts:
        raise ConflictError(
            "a watchlist result arrived for a round that never opened",
            step_id=step.step_id,
        )
    return facts


async def _publish(step: HarnessStep, deps: StepDeps, *, failed: bool) -> StepResult:
    """``agent.done`` / ``agent.failed`` of the watchlist: hold one re-flag per
    candidate in S1, record it on its case's chain and open its gate.
    """
    facts = _round_facts(step, deps)
    version = str(facts["list_to"])
    list_from = facts.get("list_from")
    fixture = bool(facts.get("fixture", False))
    clients = deps.require_oversight()
    candidates = await clients.reflags.candidates([str(c) for c in facts["added"]])
    accepted: Sequence[Mapping[str, Any]] = []
    proposal_id: Optional[str] = None
    tokens = 0
    if failed:
        payload = step.args.get("payload")
        reason = payload.get("reason") if isinstance(payload, Mapping) else None
        code = str(reason).split(":", 1)[0] if reason else "unknown"
        logger.error(
            "the watchlist agent failed; every candidate is re-flagged by code",
            extra={"case_id": step.case_id, "run_id": step.run_id, "reason": code},
        )
        await deps.append_record(
            step,
            "agent.failed",
            f"Watchlist agent failed ({code}); {plural(len(candidates), 'candidate')}"
            " re-flagged with a code-written reason",
            {"agent": "watchlist", "reason": code},
        )
    else:
        results = await deps.require_agent_results().list_for(
            step.run_id, AgentName.WATCHLIST
        )
        job_id = str(facts.get("job_id", ""))
        result = next((r for r in results if r.job_id == job_id), None)
        if result is None:
            raise ConflictError(
                f"no watchlist result is stored for job {job_id}", step_id=step.step_id
            )
        accepted = result.accepted()
        tokens = tokens_of([result])
        entry = await post_proposal(
            deps,
            step,
            result,
            kind="reflag",
            payload={
                "summary": f"{plural(len(accepted), 'proposal')} for {version}",
                "list_version": version,
                "proposals": list(accepted),
            },
            key=f"watchlist:{version}",
        )
        proposal_id = entry.proposal_id
    proposals, dropped = usable_proposals(accepted, candidates, version)
    rows = build_reflags(
        step,
        candidates,
        proposals,
        version=version,
        list_from=str(list_from) if list_from else None,
        fixture=fixture,
        proposal_id=proposal_id,
    )
    for index, chunk in enumerate(batched(rows, MAX_REFLAGS_PER_POST, strict=False)):
        await clients.reflags.post(
            ReflagsPost(list_version=version, reflags=list(chunk)),
            idempotency_key=idem(step, "reflags", str(index)),
        )
    for index, row in enumerate(rows):
        await deps.append_record(
            step,
            "reflag.proposed",
            f"Re-flag proposed: {row.jurisdiction} added to {version}"
            f" · {short(row.reason)}",
            {
                "reflag_id": row.reflag_id,
                "jurisdiction": row.jurisdiction,
                "list_version": version,
                "list_change": row.list_change,
                "proposed_by": row.proposed_by,
                "gate_id": row.gate_id,
                "fixture": row.fixture,
            },
            actor="ai" if row.proposed_by == "agent" else "system",
            ordinal=index,
            case_id=row.case_id,
        )
    logger.info(
        "re-flags proposed",
        extra={
            "case_id": step.case_id,
            "run_id": step.run_id,
            "reflags": len(rows),
            "dropped": dropped,
            "failed": failed,
        },
    )
    return StepResult(
        gates_to_open=tuple(
            GateSpec(
                gate_type=GateType.REFLAG_ACK,
                subject_key=row.reflag_id,
                subject={"kind": "reflag", "id": row.reflag_id},
                case_id=row.case_id,
            )
            for row in rows
        ),
        refs_patch={
            WATCHLIST_REF: {
                **facts,
                "reflags": len(rows),
                "dropped": dropped,
                "failed": failed,
            }
        },
        counter_deltas={"agents_run": 0 if failed else 1, "tokens_used": tokens},
    )


async def watchlist_publish(step: HarnessStep, deps: StepDeps) -> StepResult:
    """``watchlist.publish``: the list, or the agent's result, by trigger.

    Raises:
        fcrm_caseflow.steps.UnknownStepKindError: The step names a trigger this
            handler does not own.
        fcrm_common.errors.FcrmError: A peer refused or was unreachable (the
            step is retried, then parked).
    """
    trigger = step.args.get("trigger")
    if trigger == Trigger.JURISDICTION_LIST_PUBLISHED.value:
        return await _open(step, deps)
    if trigger == Trigger.AGENT_DONE_WATCHLIST.value:
        return await _publish(step, deps, failed=False)
    if trigger == Trigger.AGENT_FAILED_WATCHLIST.value:
        return await _publish(step, deps, failed=True)
    raise UnknownStepKindError(
        f"watchlist.publish cannot be started by {trigger!r}", step=step.step.value
    )


#: The watchlist handlers by step kind (merged into the oversight core's table).
HANDLERS: Final[Mapping[StepKind, StepHandler]] = {
    StepKind.WATCHLIST_PUBLISH: watchlist_publish
}

__all__ = [
    "HANDLERS",
    "build_reflags",
    "code_reason",
    "reflag_id_for",
    "usable_proposals",
    "watchlist_publish",
]
