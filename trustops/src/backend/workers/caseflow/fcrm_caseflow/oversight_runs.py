"""The runs oversight work belongs to, and the events that need no case run.

**Oversight is not a case stage.** Scene 20 asks questions of closed cases and
re-flags cases a published jurisdiction list touches; neither moves a run through
the state machine. Two events start it, and both used to be orphans, because
neither names an open run:

* ``examiner.asked`` names a **case** (S4 keeps the question). The case's own run
  is used, closed or not. A case H1 never ran (one of the 65 seeded closed cases,
  whose trails S4 holds) gets an *oversight run*: a ``case_runs`` row in state
  ``closed`` that the engine never chains from, so the examiner has a run to
  hang its job, its step and its result on.
* ``jurisdiction.list_published`` names **no case at all**. The list version
  gets one harness-level run, ``run_<digest of the version>`` on the pseudo case
  ``WATCHLIST-<version>``, which owns the watchlist job, the re-flag gates and the
  audit chain of the round (``jurisdiction.published``, the agent's guard
  records). It is derived from the version, so a redelivery finds it.

Neither event reaches the transition table: :class:`OversightRuns` finds or
creates the run and says which step-only trigger starts the work; the engine
enqueues that step in the same transaction. Everything else without a run is
still an :class:`~fcrm_caseflow.engine.OrphanEventError`.

The agent results that come back (``agent.done`` / ``agent.failed`` of the
examiner and the watchlist) are recognised here too
(:func:`oversight_result_trigger`) so that a failed examiner on a closed run
becomes a refusal instead of an illegal transition.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Final, Optional

from fcrm_common.ids import deterministic_id
from fcrm_contracts.enums import AgentName
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import ExaminerAsked, JurisdictionListPublished
from pymongo.asynchronous.client_session import AsyncClientSession

from fcrm_caseflow.store import (
    DEFAULT_PINS,
    CaseRun,
    CaseRunStore,
    new_case_run_document,
)
from fcrm_caseflow.triggers import Trigger

logger = logging.getLogger(__name__)

EXAMINER_ASKED_EVENT: Final = "examiner.asked"
JURISDICTION_EVENT: Final = "jurisdiction.list_published"

#: Events that name no open case run and are handled here instead of being
#: orphans. Any other event type without a run is still an orphan.
GLOBAL_EVENT_TYPES: Final = frozenset({EXAMINER_ASKED_EVENT, JURISDICTION_EVENT})

#: The pseudo case a list version's harness run belongs to.
WATCHLIST_CASE_PREFIX: Final = "WATCHLIST-"

#: ``CaseRun.refs`` key marking a run opened for oversight (not by a case).
OVERSIGHT_REF: Final = "oversight"

_RESULT_TRIGGERS: Final[dict[tuple[str, AgentName], Trigger]] = {
    ("agent.done", AgentName.EXAMINER): Trigger.AGENT_DONE_EXAMINER,
    ("agent.failed", AgentName.EXAMINER): Trigger.AGENT_FAILED_EXAMINER,
    ("agent.done", AgentName.WATCHLIST): Trigger.AGENT_DONE_WATCHLIST,
    ("agent.failed", AgentName.WATCHLIST): Trigger.AGENT_FAILED_WATCHLIST,
}


@dataclass(frozen=True)
class OversightTarget:
    """The run an oversight event belongs to and the step-only trigger that
    starts its work.
    """

    run: CaseRun
    trigger: Trigger


def oversight_result_trigger(envelope: Envelope) -> Optional[Trigger]:
    """The step-only trigger of an ``agent.done`` / ``agent.failed`` from the
    examiner or the watchlist, or ``None`` for any other event (including one
    whose ``agent`` is missing or unknown: the engine's own resolution refuses
    those as before).
    """
    raw = envelope.payload.get("agent")
    if not isinstance(raw, str):
        return None
    try:
        agent = AgentName(raw)
    except ValueError:
        return None
    return _RESULT_TRIGGERS.get((envelope.type, agent))


def watchlist_case_id(version: str) -> str:
    """The pseudo case id of ``version``'s harness run."""
    return f"{WATCHLIST_CASE_PREFIX}{version}"


def oversight_run_document(
    *, run_id: str, case_id: str, kind: str, refs: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """A ``case_runs`` row for oversight work: state ``closed`` with
    ``closed_at`` set, and the closing dispatch marked done, so neither
    :meth:`CaseRunStore.get_by_case_id` nor the engine's internal chain ever
    treats it as a case in flight.
    """
    document = new_case_run_document(run_id=run_id, case_id=case_id, pins=DEFAULT_PINS)
    document["state"] = "closed"
    document["closed_at"] = document["opened_at"]
    document["dispatched_in_state"] = [Trigger.CLOSED_DISPATCH.value]
    document["refs"] = {OVERSIGHT_REF: kind, **(refs or {})}
    return document


class OversightRuns:
    """Finds or creates the run an oversight event belongs to.

    Args:
        runs: ``case_runs``.
    """

    def __init__(self, runs: CaseRunStore) -> None:
        self._runs = runs

    async def target_for(
        self, envelope: Envelope, session: AsyncClientSession
    ) -> OversightTarget:
        """The run and trigger for ``envelope``.

        Raises:
            ValueError: ``envelope`` is not a global event.
            pydantic.ValidationError: its payload is not the event's contract
                (poison: retried, then parked).
        """
        if envelope.type == EXAMINER_ASKED_EVENT:
            return await self._examiner(envelope, session)
        if envelope.type == JURISDICTION_EVENT:
            return await self._watchlist(envelope, session)
        raise ValueError(f"{envelope.type} is not an oversight event")

    async def _examiner(
        self, envelope: Envelope, session: AsyncClientSession
    ) -> OversightTarget:
        asked = ExaminerAsked.model_validate(envelope.payload)
        run = await self._runs.get_any_by_case_id(asked.case_id, session=session)
        if run is None:
            run = await self._create(
                deterministic_id("run", "oversight", asked.case_id),
                asked.case_id,
                "examiner",
                session,
            )
        return OversightTarget(run, Trigger.EXAMINER_ASKED)

    async def _watchlist(
        self, envelope: Envelope, session: AsyncClientSession
    ) -> OversightTarget:
        published = JurisdictionListPublished.model_validate(envelope.payload)
        run_id = deterministic_id("run", "watchlist", published.version)
        run = await self._runs.get(run_id, session=session)
        if run is None:
            run = await self._create(
                run_id,
                watchlist_case_id(published.version),
                "watchlist",
                session,
                trace_id=envelope.trace_id,
            )
        return OversightTarget(run, Trigger.JURISDICTION_LIST_PUBLISHED)

    async def _create(
        self,
        run_id: str,
        case_id: str,
        kind: str,
        session: AsyncClientSession,
        *,
        trace_id: Optional[str] = None,
    ) -> CaseRun:
        document = oversight_run_document(run_id=run_id, case_id=case_id, kind=kind)
        document["trace_id"] = trace_id
        run = await self._runs.create(document, session=session)
        logger.info(
            "oversight run opened",
            extra={"case_id": case_id, "run_id": run_id, "kind": kind},
        )
        return run
