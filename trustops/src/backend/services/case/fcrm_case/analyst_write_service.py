"""The analyst's write use cases (scenes 8-16): decide an item, acknowledge a
boundary or band change, confirm the fast lane, answer the completeness gate, ask
the owner a round of questions or an ad-hoc question at any time (ADR-73), edit
and lock the recommendation, record a planned control or a gap, re-tag.

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
from fcrm_common.metrics import FcrmMetrics, get_metrics
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_analyst_clause import ClauseAddBody, ClauseAddResult
from fcrm_contracts.api.case_analyst_write import (
    AcknowledgeBody,
    AcknowledgeResult,
    ChangeTypeBody,
    ChangeTypeResult,
    CompletenessBody,
    CompletenessResult,
    FastLaneBody,
    GapBody,
    GateWriteResult,
    ItemDecisionBody,
    ItemDecisionResult,
    PlannedControlBody,
    QuestionsBody,
    QuestionsResult,
    Recommendation,
    RecommendationEditBody,
    RecordedWrite,
    RerunBody,
    RerunResult,
    ResolveThreadBody,
    RoundBody,
    RoundQuestion,
    ThreadResolveResult,
    UnlockBody,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import CaseState, GateType, ItemKind
from pydantic import ValidationError as PydanticValidationError

from fcrm_case.analyst_clause_service import AnalystClauseService
from fcrm_case.analyst_errors import (
    GateAlreadyDecidedError,
    ItemSupersededError,
    NoOpenGateError,
    NotLockedError,
    NotReadyError,
    RecommendationLockedError,
    RoundAlreadyOpenError,
    WrongStageError,
)
from fcrm_case.analyst_write_store import AnalystCommitted, AnalystWriteStore
from fcrm_case.assessment_store import AssessmentStore
from fcrm_case.domain.activity import names_a_restricted_term
from fcrm_case.domain.analyst_commands import (
    AGENT_FOR_KIND,
    OVERRIDING_DECISIONS,
    AdhocAsk,
    AnalystCommand,
    ack_command,
    ack_kind_of,
    ask_adhoc_command,
    ask_round_command,
    decide_item_command,
    decision_fingerprint,
    edit_recommendation_command,
    fast_lane_command,
    item_fingerprint,
    lock_command,
    open_ack_snapshots,
    plan_item_decision,
    proceed_command,
    resolve_thread_command,
    round_fingerprint,
    unlock_command,
)
from fcrm_case.domain.analyst_records import (
    gap_command,
    planned_control_command,
    rerun_command,
    retag_command,
)
from fcrm_case.domain.assessment import (
    CompletenessViewRow,
    RecommendationRow,
    current_items,
    resolve_item,
)
from fcrm_case.domain.clarifications import (
    ClarificationThreadRow,
    adhoc_question_id,
    adhoc_threads,
    anchor_parts,
    ask_blocked_reason,
    is_field_id,
    round_threads,
)
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
from fcrm_case.po_text import reject_reserved_terms
from fcrm_case.policy_config import PolicyConfig
from fcrm_case.reference import control_catalogue
from fcrm_case.sse import ActivityWriter
from fcrm_case.store import CaseStore
from fcrm_case.views_store import ViewStore

logger = logging.getLogger(__name__)

_NO_RUN: Final = "The assistant has not opened this case yet. Try again in a moment."
#: The one clarification round of a case (D-16).
FIRST_ROUND: Final = 1


class AnalystWriteService:
    """The analyst's operations over S1's stores.

    Args:
        cases: The case store (the case document).
        views: The view store (the review items).
        assessment: The assessment store (screening, completeness, recommendation).
        store: The analyst write store (atomic writes, the ledger).
        writer: Publishes committed activity rows to the case stream.
        config: The field sets by version: a re-tag resolves the type's fields
            against the field set the case pinned.
        metrics: The instrument set ``fcrm_override_total`` is recorded on; the
            process-wide one when omitted (tests inject their own).
    """

    def __init__(
        self,
        *,
        cases: CaseStore,
        views: ViewStore,
        assessment: AssessmentStore,
        store: AnalystWriteStore,
        writer: ActivityWriter,
        config: PolicyConfig,
        metrics: Optional[FcrmMetrics] = None,
    ) -> None:
        self._cases = cases
        self._views = views
        self._assessment = assessment
        self._store = store
        self._writer = writer
        self._config = config
        self._metrics = metrics if metrics is not None else get_metrics()
        self._clauses = AnalystClauseService(
            views=views,
            assessment=assessment,
            store=store,
            writer=writer,
            config=config,
        )

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

    def _count_override(self, kind: ItemKind, decision: str) -> None:
        """Count one committed decision that disagreed with an agent's proposal
        (``fcrm_override_total{agent}``).

        Called only after the commit and never for a replay, so a retry is not
        counted twice. A metric failure is logged and never reaches the request.
        """
        if decision not in OVERRIDING_DECISIONS:
            return
        agent = AGENT_FOR_KIND.get(kind)
        if agent is None:
            logger.debug(
                "override not counted: no agent proposes this kind",
                extra={"item_kind": kind.value, "decision": decision},
            )
            return
        try:
            self._metrics.override_total.add(1, {"agent": agent.value})
        except (ValueError, TypeError, KeyError, AttributeError):
            logger.warning(
                "override metric not recorded",
                extra={"agent": agent.value, "decision": decision},
                exc_info=True,
            )

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
        row = resolve_item(items, item_id)
        if row is None:
            raise NotFoundError(f"no such item {item_id} on case {case_id}")
        if row not in current_items(items):
            raise ItemSupersededError(
                f"item {item_id} was replaced by a re-run; decide its replacement",
                item_id=item_id,
            )
        item_id = row.item_id
        if row.gate_id is None:
            # Only a clause the analyst added has no gate and still takes a
            # decision (ADR-74); anything else is refused there.
            return await self._clauses.decide(principal, record, row, body)
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
        if not committed.replayed:
            self._count_override(row.kind, body.decision)
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

    # -- completeness and the one clarification round (scene 10) -----------------

    async def _completeness_view(self, case_id: str) -> CompletenessViewRow:
        view = await self._assessment.get_completeness(case_id)
        if view is None:
            raise NotReadyError(
                "the completeness report is still being drafted", case_id=case_id
            )
        return view

    async def decide_completeness(
        self, principal: Principal, case_id: str, body: CompletenessBody
    ) -> CompletenessResult:
        """Ask the owner one round of questions or proceed to review.

        ``ask`` opens round 1 (the questions in the body, else the narrative
        agent's kept ones), pauses the analyst clock (Caseflow's flag) and emits
        ``gate.decided completeness{ask}`` and ``case.clarification_sent``;
        ``proceed`` emits the gate decision only.

        Raises:
            NotReadyError: The completeness report is not drafted yet.
            RoundAlreadyOpenError: A round was already sent, with other questions.
            GateAlreadyDecidedError: The gate was decided the other way.
            ValidationError: There is no question to ask, an anchor is not the
                case's, or a question names a reserved term.
            WrongStageError: The case is not at the completeness gate.
        """
        record = await self._own_case(case_id, principal)
        view = await self._completeness_view(case_id)
        if body.decision == "proceed":
            return await self._proceed(record, view, principal)
        questions = self._questions(view, body.questions)
        return await self._ask(record, view, questions, principal)

    async def send_round(
        self, principal: Principal, case_id: str, body: RoundBody
    ) -> CompletenessResult:
        """The composer's send: the same round as ``ask``, with the questions the
        analyst kept, dropped and edited.
        """
        record = await self._own_case(case_id, principal)
        view = await self._completeness_view(case_id)
        questions = self._questions(view, body.questions)
        return await self._ask(record, view, questions, principal)

    @staticmethod
    def _questions(
        view: CompletenessViewRow, given: Optional[list[RoundQuestion]]
    ) -> list[RoundQuestion]:
        """The questions to ask: the body's, else the agent's kept ones; at
        least one, each with its own id.
        """
        pool = given
        if pool is None:
            pool = [
                RoundQuestion(
                    id=str(q["question_id"]),
                    text=str(q["text"]),
                    anchor=q.get("anchored_to"),
                    keep=q.get("dropped_reason") is None,
                )
                for q in view.questions
            ]
        kept = [q for q in pool if q.keep]
        if not kept:
            raise ValidationError(
                "Ask needs at least one question; proceed if nothing is missing.",
                errors=[{"loc": "body.questions", "msg": "no question kept"}],
            )
        if len({q.id for q in kept}) != len(kept):
            raise ValidationError(
                "Each question needs its own id.",
                errors=[{"loc": "body.questions", "msg": "duplicate question id"}],
            )
        return kept

    async def _proceed(
        self, record: CaseRecord, view: CompletenessViewRow, principal: Principal
    ) -> CompletenessResult:
        fingerprint = decision_fingerprint("proceed")
        replayed = await self._replayed(view.gate_id, fingerprint)
        if not replayed:
            self._require_stage(
                record,
                CaseState.COMPLETENESS,
                "the completeness gate is decided first",
            )
            committed = await self._commit(
                proceed_command(
                    case_id=record.case_id,
                    run_id=self._run_id(record, view.run_id),
                    gate_id=view.gate_id,
                    by=principal.sub,
                    by_name=principal.name,
                    now=utcnow(),
                )
            )
            replayed = committed.replayed
        return CompletenessResult(
            case_id=record.case_id, decision="proceed", replayed=replayed
        )

    async def _ask(
        self,
        record: CaseRecord,
        view: CompletenessViewRow,
        questions: list[RoundQuestion],
        principal: Principal,
    ) -> CompletenessResult:
        known = await self._store.get_decision(view.gate_id)
        if known is not None and known.fingerprint != round_fingerprint(questions):
            if known.decision == "ask":
                raise RoundAlreadyOpenError(
                    "one round was already sent, with other questions (D-16)",
                    case_id=record.case_id,
                )
            raise GateAlreadyDecidedError(
                f"this gate was already decided ({known.decision})",
                gate_id=view.gate_id,
            )
        if known is not None:
            thread_ids = [
                t.thread_id
                for t in round_threads(await self._store.threads(record.case_id))
            ]
            return CompletenessResult(
                case_id=record.case_id,
                decision="ask",
                round=FIRST_ROUND,
                questions=len(thread_ids),
                thread_ids=thread_ids,
                replayed=True,
            )
        self._require_stage(
            record, CaseState.COMPLETENESS, "the completeness gate is decided first"
        )
        labels = {d.field_id: d.label for d in record.required_fields}
        await self._check_questions(
            record,
            [(f"Question {q.id}", q.text, q.anchor) for q in questions],
            labels,
        )
        command = ask_round_command(
            case_id=record.case_id,
            run_id=self._run_id(record, view.run_id),
            gate_id=view.gate_id,
            round_no=FIRST_ROUND,
            questions=questions,
            labels=labels,
            by=principal.sub,
            by_name=principal.name,
            now=utcnow(),
        )
        committed = await self._commit(command)
        return CompletenessResult(
            case_id=record.case_id,
            decision="ask",
            round=FIRST_ROUND,
            questions=len(command.threads),
            thread_ids=[t.thread_id for t in command.threads],
            replayed=committed.replayed,
        )

    async def _check_questions(
        self,
        record: CaseRecord,
        questions: list[tuple[str, str, Optional[str]]],
        labels: dict[str, str],
    ) -> None:
        """Each question (``(what, text, anchor)``) is read by the owner, so it may
        not name a reserved term, and each anchor must be a field of the case or
        one of its controls.
        """
        controls = {
            str(r.subject.get("control_id"))
            for r in await self._views.list_items(record.case_id, ItemKind.CONTROL)
        }
        # A control of a domain in scope is the case's even when the assessment
        # rated none of that domain (a live run proposed no channel control, and
        # the narrative agent rightly asked about the channel's controls).
        controls |= {
            control_id
            for control_id, ref in control_catalogue().items()
            if ref.domain in record.domains
        }
        for what, text, anchor in questions:
            reject_reserved_terms(text, what, loc="body.questions")
            for part in anchor_parts(anchor):
                if not ((is_field_id(part) and part in labels) or part in controls):
                    raise ValidationError(
                        f"{part} is not a field or a control of this case.",
                        errors=[{"loc": "body.questions", "msg": "unknown anchor"}],
                    )

    # -- ad-hoc questions to the owner, at any stage (ADR-73) --------------------

    async def ask_owner(
        self, principal: Principal, case_id: str, body: QuestionsBody
    ) -> QuestionsResult:
        """Ask the owner one or more questions outside the round: one thread per
        question, then ``case.question_asked``, committed together. No gate is
        decided and the analyst's clock keeps running, so the round (D-16) is
        untouched. A question an open ad-hoc thread already asks, with the same
        anchor, reuses that thread; when every question does, nothing is written
        (a replay).

        Raises:
            WrongStageError: The case is at a stage where no question may be asked
                (not yet with the analyst, locked, with the committee).
            ValidationError: A question repeats another of the body, names a
                reserved term the owner may not read, or is anchored to something
                that is not the case's.
            NotReadyError: Caseflow has not opened a run for the case yet.
        """
        record = await self._own_case(case_id, principal)
        blocked = ask_blocked_reason(record)
        if blocked is not None:
            raise WrongStageError(
                f"{blocked} (the case is at {record.state.value})",
                case_id=case_id,
                stage=record.state.value,
            )
        asked = [(q.text, q.anchor) for q in body.questions]
        if len(set(asked)) != len(asked):
            raise ValidationError(
                "Each question may be asked once per send.",
                errors=[{"loc": "body.questions", "msg": "duplicate question"}],
            )
        labels = {d.field_id: d.label for d in record.required_fields}
        await self._check_questions(
            record,
            [
                (f"Question {n}", q.text, q.anchor)
                for n, q in enumerate(body.questions, start=1)
            ],
            labels,
        )
        existing = adhoc_threads(await self._store.threads(case_id))
        open_by_words = {
            (t.text, t.anchor): t for t in existing if t.status == "awaiting_response"
        }
        fresh = [q for q in asked if q not in open_by_words]
        if not fresh:
            return QuestionsResult(
                case_id=case_id,
                questions=len(asked),
                thread_ids=[open_by_words[q].thread_id for q in asked],
                replayed=True,
            )
        questions = [
            AdhocAsk(adhoc_question_id(len(existing) + n), text, anchor)
            for n, (text, anchor) in enumerate(fresh, start=1)
        ]
        command = ask_adhoc_command(
            case_id=case_id,
            run_id=self._run_id(record, None),
            questions=questions,
            labels=labels,
            by=principal.sub,
            by_name=principal.name,
            now=utcnow(),
        )
        await self._commit(command)
        opened: dict[tuple[str, Optional[str]], ClarificationThreadRow] = {
            (t.text, t.anchor): t for t in command.threads
        }
        logger.info(
            "ad-hoc questions asked",
            extra={"case_id": case_id, "questions": len(command.threads)},
        )
        return QuestionsResult(
            case_id=case_id,
            questions=len(asked),
            thread_ids=[(opened.get(q) or open_by_words[q]).thread_id for q in asked],
        )

    async def resolve_thread(
        self,
        principal: Principal,
        case_id: str,
        thread_id: str,
        body: ResolveThreadBody,
    ) -> ThreadResolveResult:
        """Resolve an answered question. Resolving it again replays.

        Raises:
            NotFoundError: No such question on the case.
            ConflictError: The owner has not answered it yet.
        """
        await self._own_case(case_id, principal)
        thread = await self._store.get_thread(case_id, thread_id)
        if thread is None:
            raise NotFoundError(f"no such question {thread_id}")
        if thread.status == "resolved":
            return ThreadResolveResult(thread_id=thread_id, replayed=True)
        if thread.status != "answered":
            raise ConflictError(
                "the owner has not answered this question yet",
                case_id=case_id,
                thread_id=thread_id,
            )
        await self._commit(
            resolve_thread_command(
                case_id=case_id,
                thread=thread,
                note=body.note,
                by=principal.sub,
                by_name=principal.name,
                now=utcnow(),
            )
        )
        return ThreadResolveResult(thread_id=thread_id)

    # -- the analyst's other review actions (scenes 11-14) -----------------------

    def _in_review(self, record: CaseRecord, what: str) -> None:
        self._require_stage(record, CaseState.REVIEW, f"{what} during review")

    async def add_planned_control(
        self, principal: Principal, case_id: str, body: PlannedControlBody
    ) -> RecordedWrite:
        """Record a planned control: ``case.control_planned``. It is a row of its
        own, never a review item, so it never enters the score request (rule 7).
        Recording the same one again replays.

        Raises:
            ValidationError: ``control_id`` is not a control of this case.
            WrongStageError: The case is not in review.
        """
        record = await self._own_case(case_id, principal)
        self._in_review(record, "a planned control is recorded")
        if body.control_id is not None:
            known = {
                str(r.subject.get("control_id"))
                for r in await self._views.list_items(case_id, ItemKind.CONTROL)
            }
            if body.control_id not in known:
                raise ValidationError(
                    f"{body.control_id} is not a control of this case.",
                    errors=[{"loc": "body.control_id", "msg": "unknown control"}],
                )
        command, planned_id = planned_control_command(
            case_id=case_id,
            run_id=self._run_id(record, None),
            body=body,
            by=principal.sub,
            by_name=principal.name,
            now=utcnow(),
        )
        committed = await self._commit(command)
        return RecordedWrite(
            id=planned_id, case_id=case_id, replayed=committed.replayed
        )

    async def record_gap(
        self, principal: Principal, case_id: str, body: GapBody
    ) -> RecordedWrite:
        """Record a policy gap: ``case.gap_recorded``. Recording the same words
        again replays.

        Raises:
            WrongStageError: The case is not in review.
        """
        record = await self._own_case(case_id, principal)
        self._in_review(record, "a gap is recorded")
        command, gap_id = gap_command(
            case_id=case_id,
            run_id=self._run_id(record, None),
            body=body,
            by=principal.sub,
            by_name=principal.name,
            now=utcnow(),
        )
        committed = await self._commit(command)
        return RecordedWrite(id=gap_id, case_id=case_id, replayed=committed.replayed)

    async def retag(
        self, principal: Principal, case_id: str, body: ChangeTypeBody
    ) -> ChangeTypeResult:
        """Add a change type (adds only, D-03): the type joins the case, the
        fields and domains that follow are stored in the same transaction, and
        ``case.retagged`` carries the reason. A type the case already has replays.

        Raises:
            WrongStageError: The case is not in review.
        """
        record = await self._own_case(case_id, principal)
        if body.change_type in record.change_types:
            return ChangeTypeResult(
                case_id=case_id,
                change_types=list(record.change_types),
                required_fields=len(record.required_fields),
                replayed=True,
            )
        self._in_review(record, "a change type is added")
        await self._config.load_field_set(record.fieldset_v)
        await self._commit(
            retag_command(
                case_id=case_id,
                run_id=self._run_id(record, None),
                change_type=body.change_type,
                reason=body.reason,
                by=principal.sub,
                by_name=principal.name,
            )
        )
        fresh = await self._cases.get(case_id, with_versions=False)
        after = fresh if fresh is not None else record
        return ChangeTypeResult(
            case_id=case_id,
            change_types=list(after.change_types),
            required_fields=len(after.required_fields),
        )

    async def request_rerun(
        self, principal: Principal, case_id: str, body: RerunBody
    ) -> RerunResult:
        """Ask Caseflow to run research and assessment again, with the reason:
        ``case.rerun_requested``. The same reason and scope again replays.

        Raises:
            WrongStageError: The case is not in review.
        """
        record = await self._own_case(case_id, principal)
        self._in_review(record, "a re-run is requested")
        committed = await self._commit(
            rerun_command(
                case_id=case_id,
                run_id=self._run_id(record, None),
                body=body,
                by=principal.sub,
                by_name=principal.name,
                now=utcnow(),
            )
        )
        return RerunResult(
            case_id=case_id, scope=body.scope, replayed=committed.replayed
        )

    # -- a clause from the corpus (ADR-74) -----------------------------------------

    async def add_clause(
        self, principal: Principal, case_id: str, body: ClauseAddBody
    ) -> ClauseAddResult:
        """Add a clause found by searching the corpus to the case, accepted, its
        text read from S3 at the pinned corpus version
        (:class:`~fcrm_case.analyst_clause_service.AnalystClauseService`).
        """
        record = await self._own_case(case_id, principal)
        return await self._clauses.add(principal, record, body)
