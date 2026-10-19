"""``fcrm_agent_worker.jobs.AgentJobStore`` (ADR-79): claim, renew, finish and
release an H2 job against an in-memory collection that evaluates the store's
own update pipelines (``$cond``, ``$and``, ``$ne``, ``$lt``, ``$add``,
``$literal`` and the server clock ``$$NOW``, which the test moves), so the
take-or-leave rules are exercised as written, not re-stated in a fake.
"""

from __future__ import annotations

import copy
from datetime import datetime, timedelta
from typing import Any, Optional

import pytest
from fcrm_agent_worker.jobs import (
    AGENT_JOBS_COLLECTION,
    FINISHED_TTL,
    AgentJobStore,
    ClaimOutcome,
    JobInFlightError,
    lease_seconds,
)
from fcrm_contracts.enums import Tier
from pymongo import ReturnDocument

_MISSING: Any = object()


class _UpdateResult:
    def __init__(self, modified: int) -> None:
        self.modified_count = modified


class PipelineJobsCollection:
    """``agent_jobs`` in memory: equality filters, pipeline updates (with
    upsert on ``find_one_and_update``) and a settable ``$$NOW``."""

    name = AGENT_JOBS_COLLECTION

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.now = datetime(2026, 10, 19, 12, 0, 0)
        self.sessions: list[Any] = []
        self.indexes: list[Any] = []
        self.existing_indexes: dict[str, list[tuple[str, int]]] = {"_id_": [("_id", 1)]}

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    def _eval(self, expr: Any, doc: dict[str, Any]) -> Any:
        if isinstance(expr, str):
            if expr == "$$NOW":
                return self.now
            if expr.startswith("$"):
                return doc.get(expr[1:], _MISSING)
            return expr
        if isinstance(expr, dict) and len(expr) == 1:
            ((op, args),) = expr.items()
            return self._operator(op, args, doc)
        return expr

    def _operator(self, op: str, args: Any, doc: dict[str, Any]) -> Any:
        if op == "$literal":
            return args
        if op == "$cond":
            test, yes, no = args
            return self._eval(yes if self._eval(test, doc) else no, doc)
        if op == "$and":
            return all(self._eval(a, doc) for a in args)
        left, right = (self._eval(a, doc) for a in args)
        if op == "$ne":
            return left != right
        if op == "$lt":
            # Missing and null sort below any date (BSON comparison order).
            return left is _MISSING or left is None or left < right
        if op == "$add":
            return left + timedelta(milliseconds=right)
        raise NotImplementedError(op)

    def _apply(self, pipeline: list[dict[str, Any]], doc: dict[str, Any]) -> None:
        for stage in pipeline:
            before = dict(doc)
            for key, expr in stage["$set"].items():
                value = self._eval(expr, before)
                if value is _MISSING:
                    doc.pop(key, None)
                else:
                    doc[key] = value

    def _match(self, query: dict[str, Any]) -> Optional[dict[str, Any]]:
        doc = self.docs.get(query["_id"])
        if doc is None or any(doc.get(k) != v for k, v in query.items()):
            return None
        return doc

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        pipeline: list[dict[str, Any]],
        *,
        upsert: bool,
        return_document: Any,
    ) -> Optional[dict[str, Any]]:
        assert set(query) == {"_id"}, "an upsert must be keyed on _id alone"
        assert upsert
        assert return_document is ReturnDocument.AFTER
        doc = self.docs.setdefault(query["_id"], {"_id": query["_id"]})
        self._apply(pipeline, doc)
        return copy.deepcopy(doc)

    async def update_one(
        self, query: dict[str, Any], pipeline: list[dict[str, Any]], session: Any = None
    ) -> _UpdateResult:
        self.sessions.append(session)
        doc = self._match(query)
        if doc is None:
            return _UpdateResult(0)
        self._apply(pipeline, doc)
        return _UpdateResult(1)

    async def delete_one(self, query: dict[str, Any]) -> None:
        if self._match(query) is not None:
            del self.docs[query["_id"]]

    async def create_indexes(self, indexes: list[Any]) -> list[str]:
        self.indexes = indexes
        return [index.document["name"] for index in indexes]

    async def index_information(self) -> dict[str, Any]:
        """What ``index_information`` returns: name -> ``{"key": [(f, d)]}``."""
        return {name: {"key": list(key)} for name, key in self.existing_indexes.items()}


@pytest.fixture
def jobs_collection() -> PipelineJobsCollection:
    return PipelineJobsCollection()


def _store(collection: PipelineJobsCollection, holder: str = "h2_a") -> AgentJobStore:
    return AgentJobStore(collection, holder=holder)  # type: ignore[arg-type]


async def test_a_new_job_is_claimed_with_its_lease_and_identity(
    jobs_collection: PipelineJobsCollection,
) -> None:
    store = _store(jobs_collection)
    outcome = await store.claim(
        "job-1", 600, case_id="FCRM-2026-0151", run_id="run-1", agent="research"
    )
    assert outcome is ClaimOutcome.CLAIMED
    doc = jobs_collection.docs["job-1"]
    assert doc["status"] == "running"
    assert doc["holder"] == "h2_a"
    assert (doc["case_id"], doc["run_id"], doc["agent"]) == (
        "FCRM-2026-0151",
        "run-1",
        "research",
    )
    assert doc["started_at"] == jobs_collection.now
    assert doc["lease_until"] == jobs_collection.now + timedelta(seconds=600)


async def test_a_live_job_is_running_for_another_worker_and_for_this_one(
    jobs_collection: PipelineJobsCollection,
) -> None:
    """A second claim never takes a live lease, even from the same process (a
    duplicate delivery while the first is still running)."""
    first, other = _store(jobs_collection), _store(jobs_collection, "h2_b")
    assert await first.claim("job-1", 600) is ClaimOutcome.CLAIMED
    before = copy.deepcopy(jobs_collection.docs["job-1"])

    assert await other.claim("job-1", 600) is ClaimOutcome.RUNNING
    assert await first.claim("job-1", 600) is ClaimOutcome.RUNNING
    assert jobs_collection.docs["job-1"] == before


async def test_an_expired_lease_is_taken_over_and_the_old_claim_cannot_finish(
    jobs_collection: PipelineJobsCollection,
) -> None:
    first, other = _store(jobs_collection), _store(jobs_collection, "h2_b")
    await first.claim("job-1", 600)
    jobs_collection.advance(601)

    assert await other.claim("job-1", 600) is ClaimOutcome.CLAIMED
    assert jobs_collection.docs["job-1"]["holder"] == "h2_b"
    assert not await first.renew("job-1", 600)
    assert not await first.mark_done(
        "job-1", outcome="pass", event_id="evt_old", session="s"
    )
    assert await other.mark_done(
        "job-1", outcome="pass", event_id="evt_new", session="s"
    )
    assert jobs_collection.docs["job-1"]["event_id"] == "evt_new"


async def test_renew_extends_only_this_claims_lease(
    jobs_collection: PipelineJobsCollection,
) -> None:
    store = _store(jobs_collection)
    await store.claim("job-1", 60)
    jobs_collection.advance(50)
    assert await store.renew("job-1", 60)
    assert jobs_collection.docs["job-1"]["lease_until"] == jobs_collection.now + (
        timedelta(seconds=60)
    )
    jobs_collection.advance(30)
    assert await _store(jobs_collection, "h2_b").claim("job-1", 60) is (
        ClaimOutcome.RUNNING
    )
    assert not await _store(jobs_collection, "h2_b").renew("job-1", 60)


async def test_mark_done_finishes_once_inside_the_callers_session(
    jobs_collection: PipelineJobsCollection,
) -> None:
    store = _store(jobs_collection)
    await store.claim("job-1", 600)
    session = object()

    assert await store.mark_done(
        "job-1", outcome="done", event_id="evt_1", session=session
    )
    doc = jobs_collection.docs["job-1"]
    assert (doc["status"], doc["outcome"], doc["event_id"]) == ("done", "done", "evt_1")
    assert doc["finished_at"] == jobs_collection.now
    assert jobs_collection.sessions[-1] is session
    # Re-running the transaction callback after a commit changes nothing.
    assert not await store.mark_done(
        "job-1", outcome="pass", event_id="evt_2", session=session
    )
    assert jobs_collection.docs["job-1"]["event_id"] == "evt_1"


async def test_a_done_job_is_done_for_every_later_claim_even_past_its_lease(
    jobs_collection: PipelineJobsCollection,
) -> None:
    store = _store(jobs_collection)
    await store.claim("job-1", 60)
    await store.mark_done("job-1", outcome="blocked", event_id="evt_1", session=None)  # type: ignore[arg-type]
    jobs_collection.advance(3600)
    assert await store.claim("job-1", 60) is ClaimOutcome.DONE
    assert await _store(jobs_collection, "h2_b").claim("job-1", 60) is (
        ClaimOutcome.DONE
    )
    assert jobs_collection.docs["job-1"]["status"] == "done"


async def test_release_frees_the_job_for_an_immediate_retry(
    jobs_collection: PipelineJobsCollection,
) -> None:
    store = _store(jobs_collection)
    await store.claim("job-1", 600)
    await store.release("job-1")
    assert "job-1" not in jobs_collection.docs
    assert await _store(jobs_collection, "h2_b").claim("job-1", 600) is (
        ClaimOutcome.CLAIMED
    )
    # The old process holds no claim any more: it can neither renew nor finish.
    assert not await store.renew("job-1", 600)


async def test_release_leaves_a_finished_or_foreign_job_alone(
    jobs_collection: PipelineJobsCollection,
) -> None:
    store, other = _store(jobs_collection), _store(jobs_collection, "h2_b")
    await store.claim("job-1", 600)
    await store.mark_done("job-1", outcome="pass", event_id="evt_1", session=None)  # type: ignore[arg-type]
    await store.release("job-1")
    assert jobs_collection.docs["job-1"]["status"] == "done"

    await other.claim("job-2", 600)
    await store.release("job-2")
    assert jobs_collection.docs["job-2"]["holder"] == "h2_b"


async def test_a_job_never_claimed_here_cannot_be_renewed_or_finished(
    jobs_collection: PipelineJobsCollection,
) -> None:
    store = _store(jobs_collection)
    assert not await store.renew("job-x", 60)
    assert not await store.mark_done(
        "job-x",
        outcome="pass",
        event_id="evt",
        session=None,  # type: ignore[arg-type]
    )
    assert jobs_collection.docs == {}


async def test_the_indexes_are_the_lease_scan_and_the_finished_ttl(
    jobs_collection: PipelineJobsCollection,
) -> None:
    names = await _store(jobs_collection).ensure_indexes()
    assert names == ["status_lease_until", "finished_at_ttl"]
    ttl = jobs_collection.indexes[1].document
    assert ttl["expireAfterSeconds"] == int(FINISHED_TTL.total_seconds())


async def test_an_index_whose_keys_exist_under_another_name_is_left_alone(
    jobs_collection: PipelineJobsCollection,
) -> None:
    """Seen live on 19 October: an earlier build had created the lease scan as
    ``status_1_lease_until_1``; asking for the same keys under a new name is an
    ``IndexOptionsConflict``, which stopped the harness's startup."""
    jobs_collection.existing_indexes = {
        "_id_": [("_id", 1)],
        "status_1_lease_until_1": [("status", 1), ("lease_until", 1)],
    }
    names = await _store(jobs_collection).ensure_indexes()
    assert names == ["finished_at_ttl"]
    jobs_collection.existing_indexes["finished_at_ttl"] = [("finished_at", 1)]
    assert await _store(jobs_collection).ensure_indexes() == []


def test_the_lease_covers_every_attempt_of_every_part_and_the_reduce() -> None:
    """``wall_s × max_attempts × (parts + 1) + 60`` with the tier's budget."""
    assert lease_seconds(Tier.STRONG) == 180 * 2 * 2 + 60
    assert lease_seconds(Tier.STRONG, 4) == 180 * 2 * 5 + 60
    assert lease_seconds(Tier.CHEAP, 1) == 60 * 2 * 2 + 60
    assert lease_seconds(Tier.MID, 0) == lease_seconds(Tier.MID, 1)


def test_job_in_flight_names_the_job() -> None:
    error = JobInFlightError("job-7")
    assert error.job_id == "job-7"
    assert "job-7" in str(error)
