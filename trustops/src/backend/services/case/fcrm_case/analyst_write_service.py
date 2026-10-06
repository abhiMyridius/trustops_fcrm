"""The analyst's write use cases (scenes 8-16): decide an item, acknowledge a
boundary or band change, confirm the fast lane, answer the completeness gate, ask
the owner a round of questions, edit and lock the recommendation, record a
planned control or a gap, re-tag.

Each method resolves the analyst's own case, decides what the action does and
hands the store ONE :class:`~fcrm_case.domain.analyst_commands.AnalystCommand`;
the store commits the decision, its ledger row, the activity row and the outbox
event together (**store before emit**: Caseflow reads the decided value from S1).
The activity rows are published to the case stream only after that commit.

Rules this module holds:

* **A human decides.** The routes admit the analyst role only and OPA denies the
  service account; the case must be the caller's own (403 otherwise) and open
  (409 ``case_closed`` through the one shared ``ensure_open``).
* **Nothing is decided twice.** A gate is decided once; the same decision again
  replays (200/202, ``replayed``, nothing emitted), a different one is a 409.
* **Gate ids are read, never recomputed.** An item carries its gate id, a score
  snapshot the acknowledgement gate it opened, the screening and completeness
  views their gates; after an unlock an item carries a new one.
* **The stage is checked.** A decision made at a stage Caseflow has no
  transition for would be dead-lettered, so S1 refuses it first (409
  ``wrong_stage``): fail closed.
* **No score is computed here.** An override carries the replacement input; the
  score is S2's answer to it, computed by Caseflow after this decision is stored.
"""

from __future__ import annotations

import logging
from typing import Final, Optional

from fcrm_common.errors import (
    ConflictError,
    ForbiddenError,
    GateRequiredError,
    NotFoundError,
    ValidationError,
)
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_analyst_write import (
    AcknowledgeBody,
    AcknowledgeResult,
    FastLaneBody,
    GateWriteResult,
    ItemDecisionBody,
    ItemDecisionResult,
    Recommendation,
    RecommendationEditBody,
    UnlockBody,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import CaseState, GateType
from pydantic import ValidationError as PydanticValidationError

from fcrm_case.analyst_errors import (
    DecisionNotAllowedError,
    GateAlreadyDecidedError,
    ItemSupersededError,
    NoOpenGateError,
    NotLockedError,
    NotReadyError,
    RecommendationLockedError,
    WrongStageError,
)
from fcrm_case.analyst_write_store import AnalystCommitted, AnalystWriteStore
from fcrm_case.assessment_store import AssessmentStore
from fcrm_case.domain.activity import names_a_restricted_term
from fcrm_case.domain.analyst_commands import (
    AnalystCommand,
    ack_command,
    ack_kind_of,
    decide_item_command,
    decision_fingerprint,
    edit_recommendation_command,
    fast_lane_command,
    item_fingerprint,
    lock_command,
    open_ack_snapshots,
    plan_item_decision,
    unlock_command,
)
from fcrm_case.domain.assessment import RecommendationRow, current_items
from fcrm_case.domain.recommendation_edits import (
    RecommendationState,
    apply_edit,
    lock_blockers,
    state_of,
    to_recommendation,
)
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import ScoreSnapshotRow
from fcrm_case.errors import ensure_open
from fcrm_case.sse import ActivityWriter
from fcrm_case.store import CaseStore
from fcrm_case.views_store import ViewStore

logger = logging.getLogger(__name__)

_NO_RUN: Final = "The assistant has not opened this case yet. Try again in a moment."


class AnalystWriteService:
    """The analyst's operations over S1's stores.

    Args:
        cases: The case store (the case document).
        views: The view store (the review items).
        assessment: The assessment store (screening, completeness, recommendation).
        store: The analyst write store (atomic writes, the ledger).
        writer: Publishes committed activity rows to the case stream.
    """

    def __init__(
        self,
        *,
        cases: CaseStore,
        views: ViewStore,
        assessment: AssessmentStore,
        store: AnalystWriteStore,
        writer: ActivityWriter,
    ) -> None:
        self._cases = cases
        self._views = views
        self._assessment = assessment
        self._store = store
        self._writer = writer

    # -- plumbing ----------------------------------------------------------------

    async def _own_case(self, case_id: str, principal: Principal) -> CaseRecord:
        """The case, for a write: 404 when missing, 403 unless the caller holds it,
        409 ``case_closed`` once closed.
        """
        record = await self._cases.get(case_id, with_versions=False)
        if record is None:
            raise NotFoundError(f"no such case {case_id}")
        if record.analyst_id != principal.sub:
            raise ForbiddenError("only the analyst who holds this case may change it")
        ensure_open(record)
        return record

    @staticmethod
    def _require_stage(record: CaseRecord, stage: CaseState, hint: str) -> None:
        if record.state is not stage:
            raise WrongStageError(
                f"{hint} (the case is at {record.state.value})",
                case_id=record.case_id,
                stage=record.state.value,
            )

    @staticmethod
    def _run_id(record: CaseRecord, row_run_id: Optional[str]) -> str:
        run_id = row_run_id or record.run_id
        if run_id is None:
            raise NotReadyError(_NO_RUN, case_id=record.case_id)
        return run_id

    def _publish(self, committed: AnalystCommitted) -> None:
        for entry in committed.entries:
            self._writer.publish(entry)

    async def _commit(self, command: AnalystCommand) -> AnalystCommitted:
        committed = await self._store.commit(command)
        self._publish(committed)
        return committed

    async def _replayed(self, gate_id: str, fingerprint: str) -> bool:
        """Whether ``gate_id`` was decided before with this very decision.

        Raises:
            GateAlreadyDecidedError: It was decided differently.
        """
        known = await self._store.get_decision(gate_id)
        if known is None:
            return False
        if known.fingerprint != fingerprint:
            raise GateAlreadyDecidedError(
                f"this gate was already decided ({known.decision}); a decision is "
                "made once per gate",
                gate_id=gate_id,
            )
        return True

    # -- review items (Gate A) ---------------------------------------------------

    async def decide_item(
        self,
        principal: Principal,
        case_id: str,
        item_id: str,
        body: ItemDecisionBody,
    ) -> ItemDecisionResult:
        """Decide one review item: its new status and decided value are stored and
        ``gate.decided review_item`` is enqueued in the same transaction.

        Raises:
            NotFoundError: No such item on the case.
            ItemSupersededError: A re-run replaced the item.
            DecisionNotAllowedError: The item takes no such decision or has no gate.
            WrongStageError: The case is not in review.
        """
        record = await self._own_case(case_id, principal)
        items = await self._views.list_items(case_id)
        row = next((r for r in items if r.item_id == item_id), None)
        if row is None:
            raise NotFoundError(f"no such item {item_id} on case {case_id}")
        if row not in current_items(items):
            raise ItemSupersededError(
                f"item {item_id} was replaced by a re-run; decide its replacement",
                item_id=item_id,
            )
        if row.gate_id is None:
            raise DecisionNotAllowedError(
                f"item {item_id} has no gate to decide", item_id=item_id
            )
        plan = plan_item_decision(row, body)
        if await self._replayed(row.gate_id, item_fingerprint(body)):
            return ItemDecisionResult(
                item_id=item_id, status=plan.status, replayed=True
            )
        self._require_stage(
            record, CaseState.REVIEW, "items are decided once review has started"
        )
        command = decide_item_command(
            row=row,
            body=body,
            plan=plan,
            case_id=case_id,
            run_id=self._run_id(record, row.run_id),
            gate_id=row.gate_id,
            by=principal.sub,
            by_name=principal.name,
            now=utcnow(),
        )
        committed = await self._commit(command)
        logger.info(
            "item decided",
            extra={
                "case_id": case_id,
                "item_id": item_id,
                "decision": body.decision,
                "replayed": committed.replayed,
            },
        )
        return ItemDecisionResult(
            item_id=item_id, status=plan.status, replayed=committed.replayed
        )

    # -- acknowledgements (R3) ---------------------------------------------------

    async def acknowledge(
        self, principal: Principal, case_id: str, body: AcknowledgeBody
    ) -> AcknowledgeResult:
        """Acknowledge every open gate of ``body.kind``: ``gate.decided ack`` each,
        with the gate id the score that opened it recorded.

        Raises:
            NoOpenGateError: No score has opened such a gate.
        """
        record = await self._own_case(case_id, principal)
        snapshots, decided = await self._ack_state(case_id)
        opened = [
            s
            for s in open_ack_snapshots(snapshots, decided)
            if ack_kind_of(s) == body.kind
        ]
        if not opened:
            if any(
                s.gate_id in decided and ack_kind_of(s) == body.kind for s in snapshots
            ):
                return AcknowledgeResult(kind=body.kind, acknowledged=0, replayed=True)
            raise NoOpenGateError(
                f"there is no {body.kind.replace('_', ' ')} to acknowledge",
                case_id=case_id,
                kind=body.kind,
            )
        command = ack_command(
            case_id=case_id,
            kind=body.kind,
            note=body.note,
            gates=opened,
            fallback_run_id=self._run_id(record, None),
            by=principal.sub,
            by_name=principal.name,
            now=utcnow(),
        )
        committed = await self._commit(command)
        return AcknowledgeResult(
            kind=body.kind,
            acknowledged=len(committed.emitted),
            replayed=committed.replayed,
        )

    async def _ack_state(
        self, case_id: str
    ) -> tuple[list[ScoreSnapshotRow], frozenset[str]]:
        """The score snapshots that opened an acknowledgement gate and the gate
        ids the analyst already decided.
        """
        snapshots = await self._store.score_snapshots(case_id)
        decided = await self._store.decided_gate_ids(case_id)
        return snapshots, decided

    # -- the fast lane (R4) ------------------------------------------------------

    async def decide_fast_lane(
        self, principal: Principal, case_id: str, body: FastLaneBody
    ) -> GateWriteResult:
        """Confirm or decline the fast lane the screening agent proposed:
        ``gate.decided fast_lane_confirm``.

        Raises:
            NoOpenGateError: The screening agent did not propose the fast lane.
            WrongStageError: The case is not at screening.
        """
        record = await self._own_case(case_id, principal)
        view = await self._assessment.get_screening(case_id)
        if view is None or not view.fast_lane_proposed or view.gate_id is None:
            raise NoOpenGateError(
                "the screening agent did not propose the fast lane for this case",
                case_id=case_id,
            )
        confirm = body.decision == "confirm_fast_lane"
        decision = "confirm" if confirm else "decline"
        fingerprint = decision_fingerprint(decision, body.reason)
        if await self._replayed(view.gate_id, fingerprint):
            return GateWriteResult(
                case_id=case_id,
                gate_type=GateType.FAST_LANE_CONFIRM.value,
                decision=decision,
                replayed=True,
            )
        self._require_stage(
            record, CaseState.SCREENING, "the fast lane is decided at screening"
        )
        command = fast_lane_command(
            case_id=case_id,
            run_id=self._run_id(record, view.run_id),
            gate_id=view.gate_id,
            confirm=confirm,
            reason=body.reason,
            by=principal.sub,
            by_name=principal.name,
            now=utcnow(),
        )
        committed = await self._commit(command)
        return GateWriteResult(
            case_id=case_id,
            gate_type=GateType.FAST_LANE_CONFIRM.value,
            decision=decision,
            replayed=committed.replayed,
        )

    # -- the recommendation (scene 15) -------------------------------------------

    async def _recommendation_row(self, case_id: str) -> RecommendationRow:
        row = await self._assessment.get_recommendation(case_id)
        if row is None:
            raise NotReadyError(
                "the narrative has not drafted the recommendation yet",
                case_id=case_id,
            )
        return row

    @staticmethod
    def _state(case_id: str, row: RecommendationRow) -> RecommendationState:
        try:
            return state_of(row)
        except PydanticValidationError as exc:
            logger.error(
                "stored recommendation is malformed",
                extra={"case_id": case_id, "errors": exc.error_count()},
            )
            raise ConflictError(
                "the stored recommendation does not have its expected shape",
                case_id=case_id,
            ) from exc

    async def _blockers(self, case_id: str) -> list[str]:
        items = await self._views.list_items(case_id)
        snapshots, decided = await self._ack_state(case_id)
        return lock_blockers(items, snapshots, decided)

    async def _recommendation_view(self, case_id: str) -> Recommendation:
        row = await self._recommendation_row(case_id)
        state = self._state(case_id, row)
        blockers = [] if state.locked else await self._blockers(case_id)
        return to_recommendation(state, blockers)

    async def recommendation(self, case_id: str) -> Recommendation:
        """The draft with the analyst's edits applied, the lock and what blocks it.

        Raises:
            NotFoundError: No such case.
            NotReadyError: The narrative has not drafted it yet.
        """
        if not await self._cases.exists(case_id):
            raise NotFoundError(f"no such case {case_id}")
        return await self._recommendation_view(case_id)

    async def edit_recommendation(
        self, principal: Principal, case_id: str, body: RecommendationEditBody
    ) -> Recommendation:
        """Store the analyst's edits of the draft. An edit that changes nothing
        writes nothing. The business summary is what the owner reads, so one that
        names a score, band, rating or control is refused.

        Raises:
            RecommendationLockedError: It is locked; unlock it first.
            ValidationError: The summary names a restricted term.
            WrongStageError: The narrative is not at the recommendation stage.
        """
        record = await self._own_case(case_id, principal)
        row = await self._recommendation_row(case_id)
        state = self._state(case_id, row)
        if state.locked:
            raise RecommendationLockedError(
                "the recommendation is locked; unlock it with a reason to edit it",
                case_id=case_id,
            )
        self._require_stage(
            record, CaseState.RECOMMEND, "the recommendation is edited once drafted"
        )
        if body.business_summary and names_a_restricted_term(body.business_summary):
            raise ValidationError(
                "The business summary is read by the owner and may not name a "
                "score, band, rating or control.",
                errors=[{"loc": "body.business_summary", "msg": "restricted term"}],
            )
        edits = apply_edit(state, body)
        if edits is not None:
            await self._commit(
                edit_recommendation_command(
                    case_id=case_id,
                    edits=edits,
                    by=principal.sub,
                    by_name=principal.name,
                    now=utcnow(),
                )
            )
        return await self._recommendation_view(case_id)

    async def lock_recommendation(
        self, principal: Principal, case_id: str
    ) -> Recommendation:
        """Lock the recommendation: ``case.locked``. Refused (409 ``gate_required``,
        naming each blocker) unless every item is decided and every
        acknowledgement given, judged from S1's stored state, never the client's
        claim. Locking a locked recommendation replays.

        Raises:
            GateRequiredError: An item or an acknowledgement is still open.
            WrongStageError: The recommendation is not at the lock stage.
        """
        record = await self._own_case(case_id, principal)
        row = await self._recommendation_row(case_id)
        if self._state(case_id, row).locked:
            return await self._recommendation_view(case_id)
        blockers = await self._blockers(case_id)
        if blockers:
            raise GateRequiredError(
                "Not yet: " + "; ".join(blockers),
                errors=[{"loc": "lock", "msg": b} for b in blockers],
                gate_type="lock",
                case_id=case_id,
            )
        self._require_stage(
            record, CaseState.RECOMMEND, "the recommendation is locked once drafted"
        )
        await self._commit(
            lock_command(
                case_id=case_id,
                run_id=self._run_id(record, row.run_id),
                by=principal.sub,
                by_name=principal.name,
                now=utcnow(),
            )
        )
        return await self._recommendation_view(case_id)

    async def unlock_recommendation(
        self, principal: Principal, case_id: str, body: UnlockBody
    ) -> Recommendation:
        """Unlock with a reason: ``case.unlocked{unlock_reason}``. Caseflow
        supersedes the pack and gives every item a new gate. The same unlock again
        replays.

        Raises:
            NotLockedError: It is not locked.
            WrongStageError: The case is not at the recommendation stage (after
                the release, a committee answer that changes a number unlocks it).
        """
        record = await self._own_case(case_id, principal)
        row = await self._recommendation_row(case_id)
        if not self._state(case_id, row).locked:
            if row.unlock_reason == body.reason:
                return await self._recommendation_view(case_id)
            raise NotLockedError("the recommendation is not locked", case_id=case_id)
        self._require_stage(
            record,
            CaseState.RECOMMEND,
            "a locked recommendation is unlocked before its pack is released",
        )
        await self._commit(
            unlock_command(
                case_id=case_id,
                run_id=self._run_id(record, row.run_id),
                reason=body.reason,
                by=principal.sub,
                by_name=principal.name,
                now=utcnow(),
            )
        )
        return await self._recommendation_view(case_id)
