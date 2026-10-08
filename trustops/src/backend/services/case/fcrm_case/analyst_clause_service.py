"""The analyst adds a clause from a policy-corpus search to the case, and removes
one they added (ADR-74).

Rules this module holds:

* **A human decides, with a reason.** The route admits the analyst role only
  (OPA denies the service account); the case is the caller's own and open; the
  reason is at least 20 characters (the body refuses a shorter one).
* **The stage is checked.** A clause is added or removed while the analyst
  reviews (``review``) or drafts the recommendation (``recommend``) and only
  while the recommendation is unlocked; anywhere else is 409 ``wrong_stage``.
* **The text is the corpus's.** The body names the clause; S1 reads it from S3
  at the corpus version the case pinned and stores that text word for word with
  its source reference. S3 down is a 503 and nothing is stored; a clause the
  pinned corpus does not hold, or a catalogue entry whose text may not be
  quoted, is a 422.
* **One live copy.** A clause already on the case and not removed (proposed by
  research or added before) is a 409 ``clause_on_case`` naming it. A removed
  clause may be added again: it becomes a new item.
* **Replay-safe.** The same addition again (same clause, same reason) and the
  same removal again replay: nothing is stored or announced twice.
* **No score.** A clause is never a score input; nothing here asks for one.
"""

from __future__ import annotations

import logging
from typing import Optional

from fcrm_common.errors import NotFoundError, ValidationError
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_analyst_clause import ClauseAddBody, ClauseAddResult
from fcrm_contracts.api.case_analyst_write import ItemDecisionBody, ItemDecisionResult
from fcrm_contracts.api.policy import ClauseDetailOut
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import CaseState, ItemStatus

from fcrm_case.analyst_errors import (
    ClauseOnCaseError,
    DecisionNotAllowedError,
    GateAlreadyDecidedError,
    NotReadyError,
    WrongStageError,
)
from fcrm_case.analyst_write_store import AnalystWriteStore
from fcrm_case.assessment_store import AssessmentStore
from fcrm_case.domain.analyst_clauses import (
    added_reason,
    analyst_additions,
    clause_add_command,
    clause_item,
    clause_remove_command,
    is_analyst_clause,
    live_clause,
)
from fcrm_case.domain.analyst_commands import AnalystCommand
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import ReviewItemRow
from fcrm_case.policy_config import PolicyConfig
from fcrm_case.sse import ActivityWriter
from fcrm_case.views_store import ViewStore

logger = logging.getLogger(__name__)

#: The stages at which the analyst may add or remove a clause of their own.
CLAUSE_STAGES = frozenset({CaseState.REVIEW, CaseState.RECOMMEND})


class AnalystClauseService:
    """Add a clause from the corpus to a case, or remove one the analyst added.

    The caller (:class:`~fcrm_case.analyst_write_service.AnalystWriteService`)
    has already resolved the analyst's own, open case.

    Args:
        views: The view store (the review items).
        assessment: The assessment store (the recommendation and its lock).
        store: The analyst write store (atomic writes).
        writer: Publishes committed activity rows to the case stream.
        config: S3, which holds the clause text at each corpus version.
    """

    def __init__(
        self,
        *,
        views: ViewStore,
        assessment: AssessmentStore,
        store: AnalystWriteStore,
        writer: ActivityWriter,
        config: PolicyConfig,
    ) -> None:
        self._views = views
        self._assessment = assessment
        self._store = store
        self._writer = writer
        self._config = config

    async def _require_clause_stage(self, record: CaseRecord, what: str) -> None:
        """409 ``wrong_stage`` unless the case is in review or drafting its
        recommendation, unlocked.
        """
        if record.state not in CLAUSE_STAGES:
            raise WrongStageError(
                f"{what} while the case is in review or its recommendation is "
                f"drafted (the case is at {record.state.value})",
                case_id=record.case_id,
                stage=record.state.value,
            )
        recommendation = await self._assessment.get_recommendation(record.case_id)
        locked = record.stage_flags.locked or (
            recommendation is not None and recommendation.locked
        )
        if locked:
            raise WrongStageError(
                f"{what} only while the recommendation is unlocked; unlock it "
                "with a reason first",
                case_id=record.case_id,
                stage=record.state.value,
            )

    @staticmethod
    def _run_id(record: CaseRecord) -> str:
        if record.run_id is None:
            raise NotReadyError(
                "The assistant has not opened this case yet. Try again in a moment.",
                case_id=record.case_id,
            )
        return record.run_id

    async def _commit_and_publish(self, command: AnalystCommand) -> bool:
        """Commit ``command`` and publish its activity; whether it replayed."""
        committed = await self._store.commit(command)
        for entry in committed.entries:
            self._writer.publish(entry)
        return committed.replayed

    # -- add -----------------------------------------------------------------------

    async def add(
        self, principal: Principal, record: CaseRecord, body: ClauseAddBody
    ) -> ClauseAddResult:
        """Add ``body.clause_id`` to the case, accepted, with its corpus text.

        Raises:
            WrongStageError: The case is not in review or drafting an unlocked
                recommendation.
            ClauseOnCaseError: The clause is on the case and not removed.
            ValidationError: The pinned corpus has no such clause, or its text
                may not be quoted.
            fcrm_common.errors.UpstreamUnavailableError: S3 cannot be read.
        """
        case_id = record.case_id
        items = await self._views.list_items(case_id)
        live = live_clause(items, body.clause_id)
        if live is not None:
            replay = self._replay_of(record, live, body)
            if replay is not None:
                return replay
            raise self._on_case(live)
        await self._require_clause_stage(record, "a clause is added")
        run_id = self._run_id(record)
        corpus_v = record.pinned_versions.corpus_v
        clause = await self._read_clause(body.clause_id, corpus_v)
        now = utcnow()
        item = clause_item(
            case_id=case_id,
            run_id=run_id,
            clause=clause,
            reason=body.reason,
            nth=analyst_additions(items, body.clause_id),
            by=principal.sub,
            by_name=principal.name,
            now=now,
        )
        replayed = await self._commit_and_publish(
            clause_add_command(
                case_id=case_id,
                run_id=run_id,
                item=item,
                by=principal.sub,
                by_name=principal.name,
            )
        )
        logger.info(
            "clause added from the corpus",
            extra={
                "case_id": case_id,
                "clause_id": body.clause_id,
                "corpus_v": corpus_v,
                "replayed": replayed,
            },
        )
        return ClauseAddResult(
            item_id=item.item_id,
            case_id=case_id,
            clause_id=body.clause_id,
            corpus_version=f"v{corpus_v}",
            replayed=replayed,
        )

    @staticmethod
    def _replay_of(
        record: CaseRecord, live: ReviewItemRow, body: ClauseAddBody
    ) -> Optional[ClauseAddResult]:
        """The answer to the same addition again (same clause, same reason, the
        item already stored), or ``None`` when ``live`` is something else.
        """
        if not is_analyst_clause(live) or added_reason(live) != body.reason:
            return None
        return ClauseAddResult(
            item_id=live.item_id,
            case_id=record.case_id,
            clause_id=body.clause_id,
            corpus_version=f"v{live.subject.get('corpus_v')}",
            replayed=True,
        )

    @staticmethod
    def _on_case(live: ReviewItemRow) -> ClauseOnCaseError:
        clause_id = str(live.subject.get("clause_id"))
        if is_analyst_clause(live):
            how = "you added it"
        elif live.status is ItemStatus.PROPOSED:
            how = "research proposed it; accept it instead"
        else:
            how = "research proposed it and it is accepted"
        return ClauseOnCaseError(
            f"Clause {clause_id} is already on the case ({how}).",
            case_id=live.case_id,
            clause_id=clause_id,
            item_id=live.item_id,
        )

    async def _read_clause(self, clause_id: str, corpus_v: int) -> ClauseDetailOut:
        """The clause at the pinned corpus version, refused when it cannot be
        quoted. S3's own failures (503/504) pass through untouched.
        """
        try:
            clause = await self._config.clause(clause_id, corpus_v)
        except NotFoundError as exc:
            raise ValidationError(
                f"{clause_id} is not a clause of corpus v{corpus_v}, the version "
                "this case pinned.",
                errors=[{"loc": "body.clause_id", "msg": "not in the pinned corpus"}],
            ) from exc
        if not clause.quotable:
            raise ValidationError(
                f"{clause_id} is a catalogue entry whose text may not be quoted.",
                errors=[{"loc": "body.clause_id", "msg": "not quotable"}],
            )
        return clause

    # -- remove --------------------------------------------------------------------

    async def decide(
        self,
        principal: Principal,
        record: CaseRecord,
        row: ReviewItemRow,
        body: ItemDecisionBody,
    ) -> ItemDecisionResult:
        """A decision on ``row``, an item with no gate: only a clause the analyst
        added has one to take. ``remove`` (with its reason) removes it once;
        ``accept`` changes nothing (it was accepted when added).

        Raises:
            DecisionNotAllowedError: ``row`` is not an analyst's clause, or the
                decision is neither accept nor remove.
            GateAlreadyDecidedError: It was removed with another reason.
            WrongStageError: The stage does not allow it.
        """
        if not is_analyst_clause(row):
            raise DecisionNotAllowedError(
                f"item {row.item_id} has no gate to decide", item_id=row.item_id
            )
        if body.decision not in {"accept", "remove"}:
            raise DecisionNotAllowedError(
                f"a clause is accept or remove, not {body.decision}",
                item_id=row.item_id,
            )
        if row.status is ItemStatus.REMOVED:
            same = body.decision == "remove" and (
                row.decided is not None and row.decided.reason == body.reason
            )
            if not same:
                raise GateAlreadyDecidedError(
                    f"clause {row.subject.get('clause_id')} was already removed",
                    gate_id=row.item_id,
                )
            return ItemDecisionResult(
                item_id=row.item_id, status=ItemStatus.REMOVED, replayed=True
            )
        if body.decision == "accept":
            return ItemDecisionResult(
                item_id=row.item_id, status=ItemStatus.CONFIRMED, replayed=True
            )
        await self._require_clause_stage(record, "a clause you added is removed")
        assert body.reason is not None  # the body requires one for a removal
        replayed = await self._commit_and_publish(
            clause_remove_command(
                row=row,
                reason=body.reason,
                run_id=row.run_id or self._run_id(record),
                by=principal.sub,
                by_name=principal.name,
                now=utcnow(),
            )
        )
        return ItemDecisionResult(
            item_id=row.item_id, status=ItemStatus.REMOVED, replayed=replayed
        )


__all__ = ["CLAUSE_STAGES", "AnalystClauseService"]
