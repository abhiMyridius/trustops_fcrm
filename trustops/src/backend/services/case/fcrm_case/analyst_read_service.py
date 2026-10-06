"""The analyst's read use cases: the queue, the dashboard, the workbench header,
and the assessment, risk, policy and handoff views.

Every answer is built from stored rows; no model is called and nothing here
computes a score, a band or a recommendation. The service holds no state of its
own: one is built per request from the stores ``lifespan.register`` wired.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Optional

from fcrm_common.errors import NotFoundError, ValidationError
from fcrm_common.pagination import decode_cursor, encode_cursor
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_analyst_read import (
    ActivityAnalyst,
    AssessmentSummary,
    DashboardAnalyst,
    HandoffView,
    HarnessPush,
    HarnessPushResult,
    HarnessSnapshot,
    OutcomeAnalyst,
    PolicyView,
    QueueItem,
    RiskView,
    SubmissionDiff,
    SubmissionVersion,
    WorkbenchHeader,
)
from fcrm_contracts.api.case_internal_outcome import outcome_label
from fcrm_contracts.common import Page, Principal
from fcrm_contracts.tools.case_search import CaseSearchOutput
from fcrm_contracts.tools.submission_diff import SubmissionDiffOutput

from fcrm_case.analyst_read_store import (
    MAX_QUEUE_OFFSET,
    AnalystReadStore,
    QueuePage,
    QueueQuery,
)
from fcrm_case.assessment_store import AssessmentStore
from fcrm_case.domain.analyst_assessment import (
    AssessmentInputs,
    HandoffInputs,
    RiskInputs,
    assessment_summary,
    handoff_view,
    policy_view,
    risk_view,
)
from fcrm_case.domain.analyst_feed import activity_analyst, outcome_analyst
from fcrm_case.domain.analyst_submissions import (
    CaseCandidate,
    rank_cases,
    submission_diff,
    submission_version,
    tool_diff,
)
from fcrm_case.domain.analyst_views import (
    HeaderInputs,
    assessment_ready,
    dashboard,
    pending_acknowledgements,
    queue_item,
    score_visible,
    workbench_header,
)
from fcrm_case.domain.diffing import RowDiff, SubmissionVersionNotFoundError, diff_rows
from fcrm_case.domain.record import CaseRecord
from fcrm_case.outcome_store import OutcomeStore
from fcrm_case.reference import clause_catalogue, control_catalogue, seeded_outcomes
from fcrm_case.store import CaseStore
from fcrm_case.views_store import ViewStore

logger = logging.getLogger(__name__)

#: How many of the viewer's own cases the dashboard lists.
DASHBOARD_MINE_LIMIT = 50


def _seq_cursor(cursor: str) -> int:
    """The sequence number a feed cursor encodes (the window's upper edge).

    Raises:
        fcrm_common.errors.ValidationError: the cursor is not one the feed issued.
    """
    value = decode_cursor(cursor)
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValidationError(
            "Invalid pagination cursor.",
            errors=[{"loc": "query.cursor", "msg": "malformed cursor"}],
        )
    return value


def _offset(cursor: Optional[str]) -> int:
    """The queue offset a cursor names (``0`` without one).

    Raises:
        fcrm_common.errors.ValidationError: the cursor is not an offset this
            service produced.
    """
    if cursor is None:
        return 0
    value = decode_cursor(cursor)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(
            "Invalid pagination cursor.",
            errors=[{"loc": "query.cursor", "msg": "malformed cursor"}],
        )
    if not 0 <= value <= MAX_QUEUE_OFFSET:
        raise ValidationError(
            "Invalid pagination cursor.",
            errors=[{"loc": "query.cursor", "msg": "cursor out of range"}],
        )
    return value


class AnalystReadService:
    """The analyst read use cases over the case, view, assessment, outcome and
    read stores.
    """

    def __init__(
        self,
        cases: CaseStore,
        reads: AnalystReadStore,
        assessment: AssessmentStore,
        views: ViewStore,
        outcomes: OutcomeStore,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._cases = cases
        self._reads = reads
        self._assessment = assessment
        self._views = views
        self._outcomes = outcomes
        self._clock = clock

    async def case(self, case_id: str) -> CaseRecord:
        """The stored case (without its submission versions).

        Raises:
            fcrm_common.errors.NotFoundError: no such case, or a discarded draft.
        """
        record = await self._cases.get(case_id, with_versions=False)
        if record is None or record.discarded_at is not None:
            raise NotFoundError(f"no such case {case_id}")
        return record

    # -- queue, dashboard, workbench ------------------------------------------------

    async def queue(
        self,
        principal: Principal,
        *,
        scope: str,
        stage: Optional[str],
        band: Optional[int],
        limit: int,
        cursor: Optional[str],
    ) -> Page[QueueItem]:
        """One page of the analyst's queue, the cases that need them first."""
        offset = _offset(cursor)
        page = await self._reads.queue(
            QueueQuery(
                scope=scope,
                viewer_id=principal.sub,
                stage=stage,
                band=band,
                limit=limit,
                offset=offset,
            )
        )
        next_cursor = (
            encode_cursor(offset + limit) if offset + limit < page.total else None
        )
        return Page[QueueItem](
            items=[queue_item(row, principal.sub) for row in page.rows],
            next_cursor=next_cursor,
            total=page.total,
        )

    async def _mine(self, principal: Principal) -> QueuePage:
        return await self._reads.queue(
            QueueQuery(
                scope="mine",
                viewer_id=principal.sub,
                stage=None,
                band=None,
                limit=DASHBOARD_MINE_LIMIT,
                offset=0,
            )
        )

    async def dashboard(self, principal: Principal) -> DashboardAnalyst:
        """The dashboard: portfolio figures computed from the stored cases and
        scores, the viewer's own open cases, and the seed's reference tables.
        """
        figures = await self._reads.portfolio()
        mine = await self._mine(principal)
        return dashboard(
            figures,
            [queue_item(row, principal.sub) for row in mine.rows],
            list(control_catalogue().values()),
            self._clock().date(),
        )

    async def workbench(self, case_id: str) -> WorkbenchHeader:
        """The case summary header: stage, clocks, pins, what is owed (R3)."""
        record = await self.case(case_id)
        snapshot = await self._assessment.latest_score(case_id)
        completeness = await self._assessment.get_completeness(case_id)
        acks = await self._reads.acknowledgements(case_id)
        spans = await self._reads.stage_spans(case_id)
        monitor = await self._reads.get_harness(case_id)
        return workbench_header(
            HeaderInputs(
                record=record,
                snapshot=snapshot,
                review_started=record.review_started_at is not None,
                has_completeness_view=completeness is not None,
                acks=acks,
                spans=spans,
                audit_count=monitor.counters.audit_records if monitor else 0,
                audit_verified=monitor.counters.chain_verified if monitor else False,
                now=self._clock(),
            )
        )

    # -- assessment, risk, policy, handoff ------------------------------------------

    async def assessment(self, case_id: str) -> AssessmentSummary:
        """The Summary tab: submission, flags, the critic's findings, the
        completeness report and the score box (withheld until review starts).
        """
        record = await self.case(case_id)
        items = await self._views.list_items(case_id)
        return assessment_summary(
            AssessmentInputs(
                record=record,
                snapshot=await self._assessment.latest_score(case_id),
                review_started=record.review_started_at is not None,
                items=items,
                fields=await self._views.list_field_values(case_id),
                documents=await self._views.list_documents(case_id),
                intake=await self._views.get_intake_view(case_id),
                critic=await self._assessment.get_critic(case_id),
                completeness=await self._assessment.get_completeness(case_id),
            )
        )

    async def risk(self, principal: Principal, case_id: str) -> RiskView:
        """The Risk & Controls tab. Opening it is what starts review: the first
        time the case's own analyst reads it once the assessment has a score, the
        time is stored (once) and the score is shown from then on.
        """
        record = await self.case(case_id)
        snapshot = await self._assessment.latest_score(case_id)
        completeness = await self._assessment.get_completeness(case_id)
        started = record.review_started_at
        ready = assessment_ready(record, completeness is not None)
        if (
            started is None
            and snapshot is not None
            and ready
            and record.analyst_id == principal.sub
        ):
            started = await self._reads.mark_review_started(
                case_id, principal.sub, self._clock()
            )
            logger.info(
                "review started",
                extra={"case_id": case_id, "analyst": principal.sub},
            )
        return risk_view(
            RiskInputs(
                record=record,
                snapshot=snapshot,
                review_started=started is not None,
                items=await self._views.list_items(case_id),
                critic=await self._assessment.get_critic(case_id),
                completeness_present=completeness is not None,
                catalogue=control_catalogue(),
                today=self._clock().date(),
            )
        )

    async def policy(self, case_id: str) -> PolicyView:
        """The Policy tab: the clauses research proposed, each with its stored
        verbatim quote and its decision, and the gap it declared.
        """
        record = await self.case(case_id)
        completeness = await self._assessment.get_completeness(case_id)
        return policy_view(
            record,
            await self._views.list_items(case_id),
            clause_catalogue(),
            assessment_ready(record, completeness is not None),
        )

    async def handoff(self, case_id: str) -> HandoffView:
        """The Handoff tab: the pack Caseflow built, its release checks, the
        route and band and who the committee is, as stored.
        """
        record = await self.case(case_id)
        snapshot = await self._assessment.latest_score(case_id)
        acks = await self._reads.acknowledgements(case_id)
        started = record.review_started_at is not None
        shown = score_visible(snapshot, started)
        outcome = await self._outcomes.get_outcome(case_id)
        recorded = outcome if outcome is not None and outcome.outcome else None
        return handoff_view(
            HandoffInputs(
                record=record,
                snapshot=snapshot,
                review_started=started,
                items=await self._views.list_items(case_id),
                owed=pending_acknowledgements(snapshot, acks) if shown else [],
                handoff=await self._assessment.get_handoff(case_id),
                recommendation=await self._assessment.get_recommendation(case_id),
                catalogue=control_catalogue(),
                released_to=await self._reads.released_to(case_id),
                outcome_label=outcome_label(recorded.outcome)
                if recorded is not None and recorded.outcome is not None
                else None,
                outcome_at=recorded.decided_at if recorded is not None else None,
            )
        )

    # -- submissions and the service reads over them --------------------------------

    async def submissions(self, case_id: str) -> Page[SubmissionVersion]:
        """Every frozen submission of the case, oldest first, as the owner left it."""
        record = await self.case(case_id)
        rows = await self._reads.list_submissions(case_id)
        documents = {d.doc_id: d for d in await self._views.list_documents(case_id)}
        items = [submission_version(record, r, documents) for r in rows]
        return Page[SubmissionVersion](items=items, next_cursor=None, total=len(items))

    async def _diff(self, case_id: str, from_version: int, to_version: int) -> RowDiff:
        """The comparison of two frozen submissions.

        Raises:
            fcrm_common.errors.NotFoundError: either version does not exist.
        """
        rows = await self._reads.list_submissions(case_id)
        try:
            return diff_rows(rows, from_version, to_version)
        except SubmissionVersionNotFoundError as exc:
            logger.info(
                "submission version not found",
                extra={"case_id": case_id, "from": from_version, "to": to_version},
            )
            raise NotFoundError(str(exc)) from exc

    async def submission_diff(
        self, case_id: str, from_version: int, to_version: int
    ) -> SubmissionDiff:
        """What changed between two submissions: fields with before and after
        value and origin, and the documents added.
        """
        record = await self.case(case_id)
        diff = await self._diff(case_id, from_version, to_version)
        documents = {d.doc_id: d for d in await self._views.list_documents(case_id)}
        return submission_diff(record, diff, from_version, to_version, documents)

    async def tool_diff(
        self, case_id: str, from_version: int, to_version: int
    ) -> SubmissionDiffOutput:
        """The same comparison in the ``submission_diff`` tool's shape."""
        await self.case(case_id)
        return tool_diff(await self._diff(case_id, from_version, to_version))

    async def search(
        self,
        *,
        query: str,
        change_types: Sequence[str],
        top_k: int,
        closed_only: bool,
        geography: Optional[str],
    ) -> CaseSearchOutput:
        """Cases like ``query``, best first: ranked by word overlap with the
        title and overlap of change types, each with its recorded outcome (the
        seed's for a seeded case with no outcome row of its own).
        """
        found = await self._reads.search_candidates(
            change_types=change_types, geography=geography, closed_only=closed_only
        )
        seeded = seeded_outcomes()
        candidates = [
            c
            if c.outcome is not None
            else CaseCandidate(
                c.case_id, c.title, c.change_types, seeded.get(c.case_id)
            )
            for c in found
        ]
        return CaseSearchOutput(hits=rank_cases(query, change_types, candidates, top_k))

    # -- the activity feed and the outcome, as staff read them ----------------------

    async def activity(
        self, case_id: str, *, limit: int, cursor: Optional[str]
    ) -> Page[ActivityAnalyst]:
        """The case's activity, newest first, every row (owner, analyst and shared
        visibility) with who did it, the audit record it cites and its gate. A page
        is a window of ``limit`` sequence numbers; ``next_cursor`` opens the window
        before it.
        """
        record = await self.case(case_id)
        upper = record.activity_seq + 1 if cursor is None else _seq_cursor(cursor)
        lower = max(0, upper - 1 - limit)
        if upper <= 1:
            return Page[ActivityAnalyst](items=[], next_cursor=None, total=0)
        entries = await self._cases.activity.since(
            case_id, lower, audience="staff", limit=upper - 1 - lower
        )
        shown = sorted(
            (e for e in entries if e.seq < upper), key=lambda e: e.seq, reverse=True
        )
        return Page[ActivityAnalyst](
            items=[activity_analyst(e, record) for e in shown],
            next_cursor=encode_cursor(lower + 1) if lower > 0 else None,
            total=None,
        )

    async def outcome(self, case_id: str) -> OutcomeAnalyst:
        """The recorded outcome with who decided it and how, its reason and the
        conditions it activated.
        """
        record = await self.case(case_id)
        return outcome_analyst(
            record,
            await self._outcomes.get_outcome(case_id),
            await self._outcomes.list_conditions(case_id),
            seeded_outcomes(),
        )

    # -- the harness monitor --------------------------------------------------------

    async def harness(self, case_id: str) -> HarnessSnapshot:
        """The latest snapshot of the case's harness run, as Caseflow pushed it.

        Raises:
            fcrm_common.errors.NotFoundError: no such case, or no run has pushed a
                snapshot yet (the monitor shows "no harness run is recorded").
        """
        await self.case(case_id)
        snapshot = await self._reads.get_harness(case_id)
        if snapshot is None:
            raise NotFoundError(f"no harness run is recorded for case {case_id}")
        return snapshot

    async def put_harness(self, case_id: str, push: HarnessPush) -> HarnessPushResult:
        """Store the snapshot Caseflow assembled after a step.

        Raises:
            fcrm_common.errors.NotFoundError: no such case.
            fcrm_common.errors.ValidationError: the snapshot names another case.
        """
        if push.snapshot.case_id != case_id:
            raise ValidationError(
                "The snapshot names another case.",
                errors=[{"loc": "body.snapshot.case_id", "msg": "does not match"}],
            )
        await self.case(case_id)
        result = await self._reads.put_harness(case_id, push, self._clock())
        logger.info(
            "harness snapshot stored",
            extra={"case_id": case_id, "run_id": push.run_id, "lines": result.lines},
        )
        return result
