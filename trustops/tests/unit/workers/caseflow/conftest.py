"""Fixtures for H1's real-wiring tests (:mod:`fcrm_caseflow.store`,
``.gates``, ``.engine``, ``.consumers``, ``.app``).

No live Mongo, no live broker: every Mongo collection is
:class:`FakeCollection`, a minimal in-memory double supporting exactly the
operations :mod:`fcrm_caseflow.store`/``.gates``/``fcrm_common.outbox``
issue (insert_one, find_one, find, update_one, find_one_and_update,
count_documents, create_indexes) — the same style of double
``tests/unit/packages/common/test_common_outbox.py`` uses for
:class:`~fcrm_common.outbox.Outbox`/``OutboxRelay``, extended here to the
handful of extra query shapes H1's own code needs. :class:`FakeSession`
mirrors that same file's double: ``in_transaction`` is the only thing
:meth:`fcrm_common.outbox.Outbox.enqueue` checks, so it is the only thing
this fake needs to carry to prove the atomicity *contract* (every write in
one ``engine.handle()`` call passes the same session) — real commit/abort
semantics are only provable against a real replica set
(``test_engine_live.py``, skipped without ``FCRM_TEST_MONGO_URI``, mirrors
``tests/unit/packages/common/test_common_mongo_live.py``'s own pattern).
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any, Optional

import pytest
from fcrm_caseflow.audit_client import AuditClient
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.gates import GateStore
from fcrm_caseflow.store import CaseRunStore
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.api.audit import AuditRecord, RecordAppend
from fcrm_contracts.events.envelope import Envelope


class FakeSession:
    """Mirrors ``tests/unit/packages/common/test_common_outbox.py``'s own
    fake: the one attribute :meth:`~fcrm_common.outbox.Outbox.enqueue`
    reads.
    """

    def __init__(self, in_transaction: bool = True) -> None:
        self.in_transaction = in_transaction


def _matches(doc: Mapping[str, Any], filter_: Mapping[str, Any]) -> bool:
    return all(doc.get(key) == expected for key, expected in filter_.items())


class FakeCursor:
    """What ``find()`` returns: async-iterable, and ``.to_list()`` for the
    call sites (:class:`~fcrm_common.outbox.OutboxRelay`) that want it that
    way instead.
    """

    def __init__(self, docs: list[dict[str, Any]]) -> None:
        self._docs = docs

    def __aiter__(self) -> FakeCursor:
        self._iter = iter(self._docs)
        return self

    async def __anext__(self) -> dict[str, Any]:
        try:
            return next(self._iter)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def to_list(self) -> list[dict[str, Any]]:
        return list(self._docs)


class FakeCollection:
    """An in-memory double for exactly the operations this package's
    stores issue. Documents are plain dicts; ``_id`` is whatever the
    caller supplies (every call site in this package sets it explicitly).
    """

    def __init__(self, name: str = "fake") -> None:
        self.full_name = f"fcrm_harness.{name}"
        self.docs: list[dict[str, Any]] = []

    def _all(self, filter_: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if not filter_:
            return list(self.docs)
        return [d for d in self.docs if _matches(d, filter_)]

    async def insert_one(self, doc: Mapping[str, Any], session: object = None) -> None:
        self.docs.append(copy.deepcopy(dict(doc)))

    async def find_one(
        self,
        filter_: Optional[Mapping[str, Any]] = None,
        sort: Optional[Sequence[tuple[str, int]]] = None,
        projection: Optional[Mapping[str, int]] = None,
        session: object = None,
    ) -> Optional[dict[str, Any]]:
        matches = self._sorted(self._all(filter_), sort)
        return copy.deepcopy(matches[0]) if matches else None

    def find(
        self,
        filter_: Optional[Mapping[str, Any]] = None,
        sort: Optional[Sequence[tuple[str, int]]] = None,
        limit: int = 0,
        skip: int = 0,
        session: object = None,
    ) -> FakeCursor:
        matches = self._sorted(self._all(filter_), sort)[skip:]
        if limit:
            matches = matches[:limit]
        return FakeCursor([copy.deepcopy(d) for d in matches])

    @staticmethod
    def _sorted(
        docs: list[dict[str, Any]], sort: Optional[Sequence[tuple[str, int]]]
    ) -> list[dict[str, Any]]:
        if not sort:
            return docs
        result = docs
        for field, direction in reversed(sort):
            result = sorted(
                result, key=lambda d: d.get(field, 0), reverse=direction < 0
            )
        return result

    @staticmethod
    def _apply_update(doc: dict[str, Any], update: Mapping[str, Any]) -> None:
        for op, fields in update.items():
            if op == "$set":
                doc.update(dict(fields))
            elif op == "$inc":
                for key, amount in fields.items():
                    doc[key] = doc.get(key, 0) + amount
            elif op == "$pull":
                for key, value in fields.items():
                    doc[key] = [v for v in doc.get(key, []) if v != value]
            else:
                raise NotImplementedError(f"FakeCollection cannot apply {op}")

    async def update_one(
        self,
        filter_: Mapping[str, Any],
        update: Mapping[str, Any],
        upsert: bool = False,
        session: object = None,
    ) -> None:
        matches = self._all(filter_)
        if not matches:
            return
        self._apply_update(matches[0], update)

    async def find_one_and_update(
        self,
        filter_: Mapping[str, Any],
        update: Mapping[str, Any],
        sort: Optional[Sequence[tuple[str, int]]] = None,
        upsert: bool = False,
        return_document: object = None,
        session: object = None,
    ) -> Optional[dict[str, Any]]:
        matches = self._all(filter_)
        if not matches:
            return None
        self._apply_update(matches[0], update)
        return copy.deepcopy(matches[0])

    async def count_documents(
        self, filter_: Optional[Mapping[str, Any]] = None, session: object = None
    ) -> int:
        return len(self._all(filter_))

    async def create_indexes(self, indexes: Sequence[object]) -> list[str]:
        return []


class FakePublisher:
    """Records every publish; no broker. Implements ``fcrm_common.events
    .Publisher``.
    """

    def __init__(self) -> None:
        self.published: list[tuple[str, str, Envelope]] = []

    async def publish(
        self, exchange: str, routing_key: str, body: bytes, headers: dict[str, Any]
    ) -> None:
        self.published.append(
            (exchange, routing_key, Envelope.model_validate_json(body))
        )


class RecordingAudit(AuditClient):
    """An :class:`~fcrm_caseflow.audit_client.AuditClient` that never calls
    HTTP: records every :class:`RecordAppend` instead (mirrors the
    agent-worker test suite's own ``recording_audit`` fixture).
    """

    def __init__(self) -> None:
        self.records: list[RecordAppend] = []

    async def append(self, record: RecordAppend) -> AuditRecord:
        self.records.append(record)
        return AuditRecord(
            case_id=record.case_id,
            seq=len(self.records),
            type=record.type,
            actor=record.actor,
            summary=record.summary,
            data=record.data,
            prev_hash="0" * 64,
            hash="0" * 64,
            created_at=utcnow(),
        )


@pytest.fixture
def case_runs_collection() -> FakeCollection:
    return FakeCollection("case_runs")


@pytest.fixture
def run_events_collection() -> FakeCollection:
    return FakeCollection("run_events")


@pytest.fixture
def gates_collection() -> FakeCollection:
    return FakeCollection("gates")


@pytest.fixture
def outbox_collection() -> FakeCollection:
    return FakeCollection("outbox")


@pytest.fixture
def consumed_collection() -> FakeCollection:
    return FakeCollection("consumed_messages")


@pytest.fixture
def run_store(
    case_runs_collection: FakeCollection, run_events_collection: FakeCollection
) -> CaseRunStore:
    return CaseRunStore(case_runs_collection, run_events_collection)  # type: ignore[arg-type]


@pytest.fixture
def gate_store(gates_collection: FakeCollection) -> GateStore:
    return GateStore(gates_collection)  # type: ignore[arg-type]


@pytest.fixture
def outbox(outbox_collection: FakeCollection) -> Outbox:
    return Outbox(outbox_collection)  # type: ignore[arg-type]


@pytest.fixture
def recording_audit() -> RecordingAudit:
    return RecordingAudit()


@pytest.fixture
def engine(
    run_store: CaseRunStore,
    gate_store: GateStore,
    outbox: Outbox,
    recording_audit: RecordingAudit,
) -> CaseflowEngine:
    return CaseflowEngine(run_store, gate_store, outbox, recording_audit)


@pytest.fixture
def session() -> FakeSession:
    return FakeSession(in_transaction=True)
