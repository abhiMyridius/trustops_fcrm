"""The analyst's read use cases: the queue, the dashboard and the workbench header
(and, in the modules beside it, the assessment, risk, policy and handoff views).

Every answer is built from stored rows; no model is called and nothing here
computes a score, a band or a recommendation. The service holds no state of its
own: one is built per request from the stores ``lifespan.register`` wired.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Optional

from fcrm_common.errors import NotFoundError, ValidationError
from fcrm_common.pagination import decode_cursor, encode_cursor
from fcrm_common.time import utcnow
from fcrm_contracts.api.case_analyst_read import (
    DashboardAnalyst,
    QueueItem,
    WorkbenchHeader,
)
from fcrm_contracts.common import Page, Principal

from fcrm_case.analyst_read_store import (
    MAX_QUEUE_OFFSET,
    AnalystReadStore,
    QueuePage,
    QueueQuery,
)
from fcrm_case.assessment_store import AssessmentStore
from fcrm_case.domain.analyst_views import (
    HeaderInputs,
    dashboard,
    queue_item,
    workbench_header,
)
from fcrm_case.domain.record import CaseRecord
from fcrm_case.reference import control_catalogue
from fcrm_case.store import CaseStore

logger = logging.getLogger(__name__)

#: How many of the viewer's own cases the dashboard lists.
DASHBOARD_MINE_LIMIT = 50


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
    """The analyst read use cases over the case, assessment and read stores."""

    def __init__(
        self,
        cases: CaseStore,
        reads: AnalystReadStore,
        assessment: AssessmentStore,
        clock: Callable[[], datetime] = utcnow,
    ) -> None:
        self._cases = cases
        self._reads = reads
        self._assessment = assessment
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
        return workbench_header(
            HeaderInputs(
                record=record,
                snapshot=snapshot,
                review_started=record.review_started_at is not None,
                has_completeness_view=completeness is not None,
                acks=acks,
                spans=spans,
                audit_count=0,
                audit_verified=False,
                now=self._clock(),
            )
        )
