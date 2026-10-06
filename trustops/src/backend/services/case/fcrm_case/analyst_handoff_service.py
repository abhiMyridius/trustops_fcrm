"""The analyst's handoff use cases (flow F-path): see what failed, finish it by hand.

When an agent fails after its one retry Caseflow moves the run to ``handoff`` and
opens a ``handoff`` gate per failed job. :meth:`AnalystHandoffService.complete`
is how the analyst finishes one: the proposals the agent should have produced and
a reason.

Rules this module holds (the same as :mod:`fcrm_case.analyst_write_service`'s):

* **A human decides.** The route admits the analyst role only and OPA denies the
  service account; the case must be the caller's own (403) and open (409
  ``case_closed``).
* **The output is checked before it is stored**, like an agent's: the same proposal
  contracts and Guard 4's source-free checks (:mod:`fcrm_guardrails.manual_output`).
  Caseflow checks it again. A refusal is a 422 naming each problem and changes
  nothing.
* **Store before emit.** The output, the decision and ``gate.decided handoff`` (with
  the output attached) commit in one transaction.
* **Nothing is decided twice.** The same output again replays (``replayed``, nothing
  emitted); a different one is a 409.
* **The stage is checked.** A completion while the case is not in ``handoff`` would
  be dead-lettered by Caseflow, so S1 refuses it first (409 ``wrong_stage``).
* **Only a gate S1 was told of.** A gate id S1 holds no row for, or one of another
  case, is a 404: Caseflow opened it and announced it (``gate.opened``).
"""

from __future__ import annotations

import logging
from typing import Final

from fcrm_common.errors import ForbiddenError, NotFoundError, ValidationError
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_analyst_handoff import (
    HandoffCompleteBody,
    HandoffCompleteResult,
    HandoffGatesView,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import AgentName, CaseState
from fcrm_guardrails.manual_output import check_manual_output

from fcrm_case.analyst_errors import GateAlreadyDecidedError, WrongStageError
from fcrm_case.analyst_write_store import AnalystWriteStore
from fcrm_case.domain.analyst_handoff import (
    HandoffGateRow,
    gate_view,
    handoff_command,
    output_fingerprint,
)
from fcrm_case.domain.record import CaseRecord
from fcrm_case.errors import ensure_open
from fcrm_case.handoff_store import HandoffGateStore
from fcrm_case.sse import ActivityWriter
from fcrm_case.store import CaseStore

logger = logging.getLogger(__name__)

_NO_GATE: Final = "no such handoff gate on this case"


class AnalystHandoffService:
    """The analyst's handoff operations over S1's stores.

    Args:
        cases: The case store (the case document).
        handoffs: The handoff gates S1 was told of.
        store: The analyst write store (atomic writes, the ledger).
        writer: Publishes committed activity rows to the case stream.
    """

    def __init__(
        self,
        *,
        cases: CaseStore,
        handoffs: HandoffGateStore,
        store: AnalystWriteStore,
        writer: ActivityWriter,
    ) -> None:
        self._cases = cases
        self._handoffs = handoffs
        self._store = store
        self._writer = writer

    async def gates(self, case_id: str) -> HandoffGatesView:
        """The case's handoff gates, oldest first: the open ones and those already
        completed.

        Raises:
            NotFoundError: No such case.
        """
        if not await self._cases.exists(case_id):
            raise NotFoundError(f"no such case {case_id}")
        decided = await self._store.decided_gate_ids(case_id)
        rows = await self._handoffs.for_case(case_id)
        return HandoffGatesView(
            case_id=case_id,
            gates=[gate_view(row, row.gate_id in decided) for row in rows],
        )

    async def _own_case(self, case_id: str, principal: Principal) -> CaseRecord:
        record = await self._cases.get(case_id, with_versions=False)
        if record is None:
            raise NotFoundError(f"no such case {case_id}")
        if record.analyst_id != principal.sub:
            raise ForbiddenError("only the analyst who holds this case may change it")
        ensure_open(record)
        return record

    async def _gate_of(self, case_id: str, gate_id: str) -> HandoffGateRow:
        row = await self._handoffs.get(gate_id)
        if row is None or row.case_id != case_id:
            raise NotFoundError(_NO_GATE, gate_id=gate_id)
        return row

    async def _replayed(self, gate_id: str, fingerprint: str) -> bool:
        """Whether ``gate_id`` was completed before with this very output.

        Raises:
            GateAlreadyDecidedError: It was completed with another.
        """
        known = await self._store.get_decision(gate_id)
        if known is None:
            return False
        if known.fingerprint != fingerprint:
            raise GateAlreadyDecidedError(
                "this handoff was already completed; a gate is decided once",
                gate_id=gate_id,
            )
        return True

    async def complete(
        self,
        principal: Principal,
        case_id: str,
        gate_id: str,
        body: HandoffCompleteBody,
    ) -> HandoffCompleteResult:
        """Finish the failed job of ``gate_id`` by hand.

        Raises:
            NotFoundError: No such case, or S1 holds no such gate for it.
            ForbiddenError: The case is another analyst's.
            ValidationError: The output fails the checks (each problem is named).
            GateAlreadyDecidedError: The gate was completed with another output.
            WrongStageError: The case is not in ``handoff``.
        """
        record = await self._own_case(case_id, principal)
        row = await self._gate_of(case_id, gate_id)
        replayed = await self._replayed(
            gate_id, output_fingerprint(body.output, body.reason)
        )
        check = check_manual_output(AgentName(row.agent), body.output)
        if not replayed:
            if record.state is not CaseState.HANDOFF:
                raise WrongStageError(
                    "a failed step is completed while the case is in handoff "
                    f"(the case is at {record.state.value})",
                    case_id=case_id,
                    stage=record.state.value,
                )
            if not check.ok:
                raise ValidationError(
                    f"The output does not pass the checks for the {row.agent} agent.",
                    errors=[
                        {"loc": "body.output", "msg": problem}
                        for problem in check.problems
                    ],
                )
            committed = await self._store.commit(
                handoff_command(
                    row=row,
                    body=body,
                    accepted=list(check.accepted),
                    by=principal.sub,
                    by_name=principal.name or principal.sub,
                    now=utcnow(),
                )
            )
            for entry in committed.entries:
                self._writer.publish(entry)
            replayed = committed.replayed
            logger.info(
                "handoff completed by hand",
                extra={"case_id": case_id, "gate_id": gate_id, "agent": row.agent},
            )
        return HandoffCompleteResult(
            case_id=case_id,
            gate_id=gate_id,
            agent=row.agent,
            job_id=row.job_id,
            items=len(check.accepted) if check.ok else len(body.output),
            decided_by=principal.sub,
            replayed=replayed,
        )
