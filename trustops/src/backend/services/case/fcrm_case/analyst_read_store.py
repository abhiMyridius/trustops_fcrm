"""S1's analyst read store: the queries behind the queue, the dashboard and the
workbench header that no other store owns.

Same split as :mod:`fcrm_case.outcome_store`: a small :class:`AnalystReadStore`
``Protocol`` the service depends on, a Mongo implementation the lifespan wires
and an in-memory twin for unit tests that reads the same dicts the other
in-memory stores hold. The Mongo queries, each one round trip and each behind an
index:

=====================  ======================================================
``queue``              **one** aggregation over ``cases``: the scope and stage
                       filter, a ``$lookup`` of the latest score snapshot (the
                       ``{case_id, computed_at}`` index) and of the critic
                       count, the band filter, the "needs the analyst first"
                       rank, and a ``$facet`` that returns the page and the
                       total together
``portfolio``          two aggregations: one ``$facet`` over ``cases`` (closures,
                       average days, months, types) and the latest snapshot's band
                       per case
``mark_review_started``  one conditional update on the case, set once
``acknowledgements``   ``{case_id, at}`` on ``acknowledgements``
``stage_spans``        ``{case_id, seq}`` on ``activity``, ``case.stage_changed``
=====================  ======================================================

Nothing here writes a score or a stage: the one write is ``review_started_at``,
the moment the analyst first looked at the risk view.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final, Optional, Protocol

from fcrm_common.mongo import Repository
from fcrm_contracts.api.case_analyst_read import (
    HarnessPush,
    HarnessPushResult,
    HarnessSnapshot,
)
from fcrm_contracts.enums import CaseState
from pymongo import ASCENDING, AsyncMongoClient, IndexModel
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_case.domain.activity import ActivityEntry
from fcrm_case.domain.analyst_views import (
    FAST_LANE_STAGE,
    NEEDS_RANK,
    STAGE_STATES,
    PortfolioFigures,
    QueueRow,
    StageSpan,
)
from fcrm_case.domain.po_rows import SubmissionVersionRow
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import ScoreSnapshotRow

logger = logging.getLogger(__name__)

#: Most acknowledgements and stage changes one case can have; the bound stops a
#: bug from reading an unbounded log.
MAX_ACKNOWLEDGEMENTS: Final = 100
MAX_STAGE_CHANGES: Final = 500
MAX_SUBMISSIONS: Final = 100
#: A queue page is at most this many rows and starts at most this far in.
MAX_QUEUE_OFFSET: Final = 10_000

Scope = str  # "mine" | "all" | "unassigned"


@dataclass(frozen=True)
class QueueQuery:
    """What the queue is asked for."""

    scope: Scope
    viewer_id: str
    stage: Optional[str]
    band: Optional[int]
    limit: int
    offset: int


@dataclass(frozen=True)
class QueuePage:
    """One page of queue rows and the number of cases the filters match."""

    rows: tuple[QueueRow, ...]
    total: int


class AnalystReadStore(Protocol):
    """What the analyst read service needs beyond the other stores."""

    async def ensure_indexes(self) -> None:
        """Create the indexes the queries below rely on (idempotent)."""
        ...

    async def queue(self, query: QueueQuery) -> QueuePage:
        """The analyst's queue: ordered by what needs the analyst first, bounded."""
        ...

    async def portfolio(self) -> PortfolioFigures:
        """The portfolio counts the dashboard shows."""
        ...

    async def mark_review_started(
        self, case_id: str, by: str, at: datetime
    ) -> Optional[datetime]:
        """Record that ``by`` opened the risk view, unless review already
        started; returns the time review started (the earlier one on a repeat),
        or ``None`` when no such case.
        """
        ...

    async def stage_spans(self, case_id: str) -> list[StageSpan]:
        """The stretches the case spent in each state, from its stage-change log."""
        ...

    async def released_to(self, case_id: str) -> list[str]:
        """The committee members the case's latest ``pack.released`` entry names."""
        ...

    async def list_submissions(self, case_id: str) -> list[SubmissionVersionRow]:
        """The case's frozen submissions, oldest version first (bounded)."""
        ...

    async def put_harness(
        self, case_id: str, push: HarnessPush, at: datetime
    ) -> HarnessPushResult:
        """Store the snapshot H1 assembled as the case's latest (a replace: the
        same push twice leaves the same state). Staff-only data: nothing here is
        ever read for an owner.
        """
        ...

    async def get_harness(self, case_id: str) -> Optional[HarnessSnapshot]:
        """The latest snapshot H1 pushed, or ``None`` before the first."""
        ...


# -- the queue pipeline -----------------------------------------------------------


def queue_match(query: QueueQuery) -> dict[str, Any]:
    """The ``$match`` of a queue query: submitted, not discarded, in scope, and
    in the stage asked for (closed cases only when ``stage`` is ``closed``).
    """
    match: dict[str, Any] = {
        "submitted_at": {"$ne": None},
        "discarded_at": None,
    }
    if query.scope == "mine":
        match["analyst_id"] = query.viewer_id
    elif query.scope == "unassigned":
        match["analyst_id"] = None
    if query.stage == FAST_LANE_STAGE:
        match["stage_flags.fast_lane_proposed"] = True
        match["state"] = {"$ne": CaseState.CLOSED.value}
    elif query.stage is not None:
        match["state"] = {"$in": sorted(s.value for s in STAGE_STATES[query.stage])}
    else:
        match["state"] = {"$ne": CaseState.CLOSED.value}
    return match


def rank_expression() -> dict[str, Any]:
    """``$switch`` over the case state giving :data:`NEEDS_RANK`."""
    return {
        "$switch": {
            "branches": [
                {"case": {"$eq": ["$state", state.value]}, "then": rank}
                for state, rank in sorted(
                    NEEDS_RANK.items(), key=lambda kv: kv[0].value
                )
            ],
            "default": max(NEEDS_RANK.values()) + 1,
        }
    }


def queue_pipeline(query: QueueQuery) -> list[dict[str, Any]]:
    """The one aggregation behind the queue (see the module docstring)."""
    pipeline: list[dict[str, Any]] = [
        {"$match": queue_match(query)},
        {
            "$lookup": {
                "from": "score_snapshots",
                "localField": "_id",
                "foreignField": "case_id",
                "pipeline": [
                    {"$sort": {"computed_at": -1}},
                    {"$limit": 1},
                    {
                        "$project": {
                            "band": 1,
                            "composite": 1,
                            "boundary_flag": 1,
                            "purpose": 1,
                        }
                    },
                ],
                "as": "snap",
            }
        },
        {
            "$lookup": {
                "from": "analyst_decisions",
                "localField": "_id",
                "foreignField": "case_id",
                "pipeline": [
                    {
                        "$match": {
                            "gate_type": "fast_lane_confirm",
                            "decision": "confirm",
                        }
                    },
                    {"$limit": 1},
                    {"$project": {"_id": 1}},
                ],
                "as": "fast",
            }
        },
        {
            "$lookup": {
                "from": "critic_views",
                "localField": "_id",
                "foreignField": "_id",
                "pipeline": [
                    {"$project": {"n": {"$size": {"$ifNull": ["$findings", []]}}}}
                ],
                "as": "critic",
            }
        },
    ]
    if query.band is not None:
        pipeline.append({"$match": {"snap.0.band": query.band}})
    pipeline.extend(
        [
            {"$addFields": {"rank": rank_expression()}},
            {"$sort": {"rank": 1, "submitted_at": 1, "_id": 1}},
            {
                "$facet": {
                    "items": [
                        {"$skip": query.offset},
                        {"$limit": query.limit},
                        {
                            "$project": {
                                "title": 1,
                                "unit": 1,
                                "owner": 1,
                                "owner_name": 1,
                                "analyst_id": 1,
                                "analyst_name": 1,
                                "change_types": 1,
                                "state": 1,
                                "submitted_at": 1,
                                "review_started_at": 1,
                                "stage_flags.fast_lane_proposed": 1,
                                "stage_flags.locked": 1,
                                "snap": 1,
                                "critic": 1,
                                "fast": 1,
                            }
                        },
                    ],
                    "total": [{"$count": "n"}],
                }
            },
        ]
    )
    return pipeline


def queue_row_from_document(doc: dict[str, Any]) -> QueueRow:
    """One ``items`` document of the queue aggregation as a :class:`QueueRow`."""
    snap: dict[str, Any] = (doc.get("snap") or [{}])[0]
    critic: dict[str, Any] = (doc.get("critic") or [{}])[0]
    flags: dict[str, Any] = doc.get("stage_flags") or {}
    return QueueRow(
        case_id=str(doc["_id"]),
        title=str(doc["title"]),
        unit=str(doc["unit"]),
        owner=str(doc["owner"]),
        owner_name=doc.get("owner_name"),
        analyst_id=doc.get("analyst_id"),
        analyst_name=doc.get("analyst_name"),
        change_types=tuple(str(c) for c in doc.get("change_types") or ()),
        state=CaseState(doc["state"]),
        submitted_at=doc.get("submitted_at"),
        review_started=doc.get("review_started_at") is not None,
        fast_lane_proposed=bool(flags.get("fast_lane_proposed")),
        fast_lane_confirmed=bool(doc.get("fast")),
        locked=bool(flags.get("locked")),
        band=int(snap["band"]) if "band" in snap else None,
        composite=float(snap["composite"]) if "composite" in snap else None,
        boundary_flag=bool(snap.get("boundary_flag", False)),
        snapshot_purpose=snap.get("purpose"),
        critic_count=int(critic.get("n", 0)),
    )


def spans_from_changes(changes: Sequence[tuple[datetime, str]]) -> list[StageSpan]:
    """Stretches from ``(at, new state)`` pairs in order: each state lasts until
    the next change; the last is still current.
    """
    spans: list[StageSpan] = []
    for index, (at, state) in enumerate(changes):
        ended = changes[index + 1][0] if index + 1 < len(changes) else None
        spans.append(StageSpan(state=state, began=at, ended=ended))
    return spans


class MongoAnalystReadStore:
    """The real store over ``fcrm_case``'s collections.

    Args:
        client: The Mongo client.
        db: The ``fcrm_case`` database.
        cases: The ``cases`` repository.
    """

    def __init__(
        self, client: AsyncMongoClient[Any], db: Any, cases: Repository[CaseRecord]
    ) -> None:
        self._cases = cases
        self._snapshots: AsyncCollection[Any] = db["score_snapshots"]
        self._activity: AsyncCollection[Any] = db["activity"]
        self._harness: AsyncCollection[Any] = db["harness_views"]
        self._submissions: Repository[SubmissionVersionRow] = Repository(
            db["submission_versions"],
            SubmissionVersionRow,
            id_of=lambda r: r.version_id,
        )

    async def ensure_indexes(self) -> None:
        """``cases`` ``{state, submitted_at}`` for the stage-filtered queue and
        and ``acknowledgements``
        ``{case_id, at}``.
        """
        await self._cases.ensure_indexes(
            [
                IndexModel(
                    [("state", ASCENDING), ("submitted_at", ASCENDING)],
                    name="state_submitted_idx",
                ),
            ]
        )

    async def queue(self, query: QueueQuery) -> QueuePage:
        rows = await self._cases.aggregate(queue_pipeline(query))
        facet = rows[0] if rows else {"items": [], "total": []}
        total = int(facet["total"][0]["n"]) if facet["total"] else 0
        return QueuePage(
            rows=tuple(queue_row_from_document(d) for d in facet["items"]),
            total=total,
        )

    async def portfolio(self) -> PortfolioFigures:
        facets = await self._cases.aggregate(
            [
                {
                    "$facet": {
                        "closed": [
                            {
                                "$match": {
                                    "state": CaseState.CLOSED.value,
                                    "discarded_at": None,
                                }
                            },
                            {
                                "$group": {
                                    "_id": None,
                                    "n": {"$sum": 1},
                                    "avg": {
                                        "$avg": {
                                            "$dateDiff": {
                                                "startDate": "$submitted_at",
                                                "endDate": "$decided_at",
                                                "unit": "day",
                                            }
                                        }
                                    },
                                }
                            },
                        ],
                        "months": [
                            {
                                "$match": {
                                    "state": CaseState.CLOSED.value,
                                    "decided_at": {"$ne": None},
                                }
                            },
                            {
                                "$group": {
                                    "_id": {
                                        "$dateToString": {
                                            "format": "%Y-%m",
                                            "date": "$decided_at",
                                        }
                                    },
                                    "n": {"$sum": 1},
                                }
                            },
                            {"$sort": {"_id": 1}},
                        ],
                        "types": [
                            {"$match": {"discarded_at": None}},
                            {"$unwind": "$change_types"},
                            {"$group": {"_id": "$change_types", "n": {"$sum": 1}}},
                        ],
                    }
                }
            ]
        )
        facet = facets[0]
        closed: dict[str, Any] = facet["closed"][0] if facet["closed"] else {}
        average = closed.get("avg")
        band_cursor = await self._snapshots.aggregate(
            [
                {"$sort": {"case_id": 1, "computed_at": -1}},
                {"$group": {"_id": "$case_id", "band": {"$first": "$band"}}},
                {"$group": {"_id": "$band", "n": {"$sum": 1}}},
            ]
        )
        band_rows = await band_cursor.to_list()
        return PortfolioFigures(
            closed_cases=int(closed.get("n", 0)),
            avg_days_to_decision=round(float(average), 1)
            if average is not None
            else None,
            closed_by_month=tuple(
                (str(m["_id"]), int(m["n"])) for m in facet["months"]
            ),
            by_type=tuple((str(t["_id"]), int(t["n"])) for t in facet["types"]),
            bands=tuple(sorted((int(b["_id"]), int(b["n"])) for b in band_rows)),
        )

    async def mark_review_started(
        self, case_id: str, by: str, at: datetime
    ) -> Optional[datetime]:
        collection = self._cases.collection
        await collection.update_one(
            {"_id": case_id, "review_started_at": None},
            {"$set": {"review_started_at": at, "review_started_by": by}},
        )
        doc = await collection.find_one(
            {"_id": case_id}, projection={"review_started_at": 1}
        )
        if doc is None:
            return None
        started: Optional[datetime] = doc.get("review_started_at")
        return started

    async def stage_spans(self, case_id: str) -> list[StageSpan]:
        docs = await self._activity.find(
            {"case_id": case_id, "type": "case.stage_changed"},
            projection={"at": 1, "detail": 1},
            sort=[("seq", ASCENDING)],
            limit=MAX_STAGE_CHANGES,
        ).to_list()
        return spans_from_changes(
            [(d["at"], str((d.get("detail") or {}).get("stage", ""))) for d in docs]
        )

    async def released_to(self, case_id: str) -> list[str]:
        doc = await self._activity.find_one(
            {"case_id": case_id, "type": "pack.released"},
            projection={"detail": 1},
            sort=[("seq", -1)],
        )
        names = (doc.get("detail") or {}).get("released_to") if doc else None
        return [str(n) for n in names or []]

    async def list_submissions(self, case_id: str) -> list[SubmissionVersionRow]:
        return await self._submissions.find(
            {"case_id": case_id},
            sort=[("version", ASCENDING)],
            limit=MAX_SUBMISSIONS,
        )

    async def put_harness(
        self, case_id: str, push: HarnessPush, at: datetime
    ) -> HarnessPushResult:
        await self._harness.replace_one(
            {"_id": case_id},
            {
                "_id": case_id,
                "case_id": case_id,
                "run_id": push.run_id,
                "snapshot": push.snapshot.model_dump(mode="json"),
                "updated_at": at,
            },
            upsert=True,
        )
        return HarnessPushResult(case_id=case_id, lines=len(push.snapshot.log))

    async def get_harness(self, case_id: str) -> Optional[HarnessSnapshot]:
        doc = await self._harness.find_one({"_id": case_id}, projection={"snapshot": 1})
        return HarnessSnapshot.model_validate(doc["snapshot"]) if doc else None


# -- the in-memory twin -----------------------------------------------------------


_LATEST: Final = datetime.max.replace(tzinfo=UTC)


def _needs_key(row: QueueRow) -> tuple[int, datetime, str]:
    return (
        NEEDS_RANK.get(row.state, max(NEEDS_RANK.values()) + 1),
        row.submitted_at or _LATEST,
        row.case_id,
    )


class InMemoryAnalystReadStore:
    """The test twin: reads the dicts the in-memory case, assessment and activity
    stores hold, and applies the same filters and order as the pipeline.

    Args:
        records: ``InMemoryCaseStore.records``.
        snapshots: ``InMemoryAssessmentStore.snapshots`` (``snapshot_id`` to row).
        critic_counts: A callable giving a case's critic finding count.
        activity: ``InMemoryActivityLog.entries``.
        submissions: The in-memory PO store's frozen submissions.
        fast_confirmed: Whether the analyst confirmed a case's fast lane.
    """

    def __init__(
        self,
        records: dict[str, CaseRecord],
        snapshots: Callable[[], dict[str, ScoreSnapshotRow]],
        critic_counts: Callable[[str], int],
        activity: Callable[[], list[ActivityEntry]],
        submissions: Callable[[], Iterable[SubmissionVersionRow]] = lambda: (),
        fast_confirmed: Callable[[str], bool] = lambda _case_id: False,
    ) -> None:
        self._records = records
        self._snapshots = snapshots
        self._critic_counts = critic_counts
        self._activity = activity
        self._submissions = submissions
        self._fast_confirmed = fast_confirmed
        self.harness: dict[str, HarnessPush] = {}

    async def ensure_indexes(self) -> None:
        """Nothing to create."""

    def _latest(self, case_id: str) -> Optional[ScoreSnapshotRow]:
        rows = [r for r in self._snapshots().values() if r.case_id == case_id]
        return max(rows, key=lambda r: r.computed_at, default=None)

    def _matches(self, record: CaseRecord, query: QueueQuery) -> bool:
        if record.submitted_at is None or record.discarded_at is not None:
            return False
        if query.scope == "mine" and record.analyst_id != query.viewer_id:
            return False
        if query.scope == "unassigned" and record.analyst_id is not None:
            return False
        if query.stage == FAST_LANE_STAGE:
            return (
                record.stage_flags.fast_lane_proposed
                and record.state is not CaseState.CLOSED
            )
        if query.stage is not None:
            return record.state in STAGE_STATES[query.stage]
        return record.state is not CaseState.CLOSED

    def _row(self, record: CaseRecord) -> QueueRow:
        snap = self._latest(record.case_id)
        return QueueRow(
            case_id=record.case_id,
            title=record.title,
            unit=record.unit,
            owner=record.owner,
            owner_name=record.owner_name,
            analyst_id=record.analyst_id,
            analyst_name=record.analyst_name,
            change_types=tuple(c.value for c in record.change_types),
            state=record.state,
            submitted_at=record.submitted_at,
            review_started=record.review_started_at is not None,
            fast_lane_proposed=record.stage_flags.fast_lane_proposed,
            fast_lane_confirmed=self._fast_confirmed(record.case_id),
            locked=record.stage_flags.locked,
            band=snap.band if snap else None,
            composite=snap.composite if snap else None,
            boundary_flag=snap.boundary_flag if snap else False,
            snapshot_purpose=snap.purpose if snap else None,
            critic_count=self._critic_counts(record.case_id),
        )

    async def queue(self, query: QueueQuery) -> QueuePage:
        rows = [self._row(r) for r in self._records.values() if self._matches(r, query)]
        if query.band is not None:
            rows = [r for r in rows if r.band == query.band]
        rows.sort(key=_needs_key)
        page = rows[query.offset : query.offset + query.limit]
        return QueuePage(rows=tuple(page), total=len(rows))

    async def portfolio(self) -> PortfolioFigures:
        closed = [
            r
            for r in self._records.values()
            if r.state is CaseState.CLOSED and r.discarded_at is None
        ]
        spans = [
            (r.decided_at.date() - r.submitted_at.date()).days
            for r in closed
            if r.decided_at is not None and r.submitted_at is not None
        ]
        months: dict[str, int] = {}
        for r in closed:
            if r.decided_at is not None:
                key = r.decided_at.strftime("%Y-%m")
                months[key] = months.get(key, 0) + 1
        types: dict[str, int] = {}
        for r in self._records.values():
            if r.discarded_at is None:
                for t in r.change_types:
                    types[t.value] = types.get(t.value, 0) + 1
        latest: dict[str, ScoreSnapshotRow] = {}
        for snap in self._snapshots().values():
            known = latest.get(snap.case_id)
            if known is None or snap.computed_at > known.computed_at:
                latest[snap.case_id] = snap
        bands: dict[int, int] = {}
        for snap in latest.values():
            bands[snap.band] = bands.get(snap.band, 0) + 1
        return PortfolioFigures(
            closed_cases=len(closed),
            avg_days_to_decision=round(sum(spans) / len(spans), 1) if spans else None,
            closed_by_month=tuple(sorted(months.items())),
            by_type=tuple(types.items()),
            bands=tuple(sorted(bands.items())),
        )

    async def mark_review_started(
        self, case_id: str, by: str, at: datetime
    ) -> Optional[datetime]:
        record = self._records.get(case_id)
        if record is None:
            return None
        if record.review_started_at is None:
            record = record.model_copy(
                update={"review_started_at": at, "review_started_by": by}
            )
            self._records[case_id] = record
        return record.review_started_at

    async def stage_spans(self, case_id: str) -> list[StageSpan]:
        changes = [
            (e.at, str(e.detail.get("stage", "")))
            for e in sorted(self._activity(), key=lambda e: e.seq)
            if e.case_id == case_id and e.type == "case.stage_changed"
        ]
        return spans_from_changes(changes)

    async def released_to(self, case_id: str) -> list[str]:
        released = [
            e
            for e in sorted(self._activity(), key=lambda e: e.seq)
            if e.case_id == case_id and e.type == "pack.released"
        ]
        names = released[-1].detail.get("released_to") if released else None
        return [str(n) for n in names or []]

    async def list_submissions(self, case_id: str) -> list[SubmissionVersionRow]:
        rows = [r for r in self._submissions() if r.case_id == case_id]
        return sorted(rows, key=lambda r: r.version)[:MAX_SUBMISSIONS]

    async def put_harness(
        self, case_id: str, push: HarnessPush, at: datetime
    ) -> HarnessPushResult:
        self.harness[case_id] = push
        return HarnessPushResult(case_id=case_id, lines=len(push.snapshot.log))

    async def get_harness(self, case_id: str) -> Optional[HarnessSnapshot]:
        push = self.harness.get(case_id)
        return push.snapshot if push is not None else None
