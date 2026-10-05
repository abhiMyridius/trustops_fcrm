"""Fixtures for H1's real-wiring tests (:mod:`fcrm_caseflow.store`,
``.gates``, ``.engine``, ``.steps``, ``.consumers``, ``.app``).

No live Mongo, no live broker: every Mongo collection is
:class:`FakeCollection`, a minimal in-memory double supporting exactly the
operations :mod:`fcrm_caseflow.store`/``.gates``/``fcrm_common.outbox``
issue (insert_one, find_one, find, update_one, find_one_and_update,
count_documents, create_indexes, with the ``$set``/``$inc``/``$pull``/
``$addToSet``/``$setOnInsert`` operators and dotted paths the step
mechanism uses) — the same style of double
``tests/unit/packages/common/test_common_outbox.py`` uses for
:class:`~fcrm_common.outbox.Outbox`/``OutboxRelay``, extended here to the
handful of extra query shapes H1's own code needs. :class:`FakeSession`
mirrors that same file's double: ``in_transaction`` is the only thing
:meth:`fcrm_common.outbox.Outbox.enqueue` checks, so it is the only thing
this fake needs to carry to prove the atomicity *contract* (every write in
one ``engine.handle()`` call passes the same session, which
``FakeCollection.sessions`` records) — real commit/abort semantics are only
provable against a real replica set (``test_engine_live.py``, skipped
without ``FCRM_TEST_MONGO_URI``, mirrors
``tests/unit/packages/common/test_common_mongo_live.py``'s own pattern).
"""

from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Optional, TypeVar

import pytest
from fcrm_caseflow.audit_client import AuditClient
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.gates import GateStore
from fcrm_caseflow.store import CaseRunStore
from fcrm_caseflow.triggers import Trigger
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.api.audit import AuditRecord, RecordAppend
from fcrm_contracts.enums import StepKind
from fcrm_contracts.events.envelope import Envelope
from pymongo.errors import DuplicateKeyError

R = TypeVar("R")


class FakeSession:
    """Mirrors ``tests/unit/packages/common/test_common_outbox.py``'s own
    fake: the one attribute :meth:`~fcrm_common.outbox.Outbox.enqueue`
    reads.
    """

    def __init__(self, in_transaction: bool = True) -> None:
        self.in_transaction = in_transaction


_MISSING = object()


def _lookup(doc: Mapping[str, Any], path: str) -> Any:
    """``doc[a][b]`` for ``"a.b"``, or ``_MISSING``."""
    node: Any = doc
    for part in path.split("."):
        if not isinstance(node, Mapping) or part not in node:
            return _MISSING
        node = node[part]
    return node


def _matches(doc: Mapping[str, Any], filter_: Mapping[str, Any]) -> bool:
    for key, expected in filter_.items():
        found = _lookup(doc, key)
        actual = None if found is _MISSING else found
        if actual != expected:
            return False
    return True


def _set_path(doc: dict[str, Any], path: str, value: Any) -> None:
    parts = path.split(".")
    node = doc
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _get_or(doc: dict[str, Any], path: str, default: Any) -> Any:
    found = _lookup(doc, path)
    return default if found is _MISSING else found


def _op_set(doc: dict[str, Any], path: str, value: Any) -> None:
    _set_path(doc, path, copy.deepcopy(value))


def _op_inc(doc: dict[str, Any], path: str, amount: Any) -> None:
    _set_path(doc, path, _get_or(doc, path, 0) + amount)


def _op_pull(doc: dict[str, Any], path: str, value: Any) -> None:
    current = _get_or(doc, path, [])
    _set_path(doc, path, [v for v in current if v != value])


def _op_add_to_set(doc: dict[str, Any], path: str, value: Any) -> None:
    current = list(_get_or(doc, path, []))
    if value not in current:
        current.append(value)
    _set_path(doc, path, current)


def _op_ignore(doc: dict[str, Any], path: str, value: Any) -> None:
    """``$setOnInsert`` only means something on the upsert path."""


_UPDATE_OPERATORS: dict[str, Callable[[dict[str, Any], str, Any], None]] = {
    "$set": _op_set,
    "$inc": _op_inc,
    "$pull": _op_pull,
    "$addToSet": _op_add_to_set,
    "$setOnInsert": _op_ignore,
}


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


@dataclass(frozen=True)
class FakeUpdateResult:
    """The two fields of PyMongo's ``UpdateResult`` this package reads."""

    matched_count: int
    upserted_id: Any = None


class FakeCollection:
    """An in-memory double for exactly the operations this package's
    stores issue. Documents are plain dicts; ``_id`` is whatever the
    caller supplies (every call site in this package sets it explicitly).

    ``unique`` lists field tuples that must be unique across documents (a
    unique index): inserting a duplicate raises ``DuplicateKeyError``, like
    Mongo does. A duplicate ``_id`` always does. ``sessions`` records the
    session every write was issued with.
    """

    def __init__(
        self, name: str = "fake", unique: Sequence[tuple[str, ...]] = ()
    ) -> None:
        self.full_name = f"fcrm_harness.{name}"
        self.docs: list[dict[str, Any]] = []
        self.sessions: list[object] = []
        self.indexes: list[Any] = []  # the IndexModels ensure_indexes asked for
        self._unique = tuple(unique)

    def _all(self, filter_: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
        if not filter_:
            return list(self.docs)
        return [d for d in self.docs if _matches(d, filter_)]

    def _check_unique(self, doc: Mapping[str, Any]) -> None:
        if "_id" in doc and any(d.get("_id") == doc["_id"] for d in self.docs):
            raise DuplicateKeyError(f"duplicate _id {doc['_id']!r}")
        for fields in self._unique:
            key = tuple(_lookup(doc, f) for f in fields)
            if _MISSING in key:
                continue
            for existing in self.docs:
                if tuple(_lookup(existing, f) for f in fields) == key:
                    raise DuplicateKeyError(f"duplicate {fields} = {key!r}")

    async def insert_one(self, doc: Mapping[str, Any], session: object = None) -> None:
        self.sessions.append(session)
        self._check_unique(doc)
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
            operator = _UPDATE_OPERATORS.get(op)
            if operator is None:
                raise NotImplementedError(f"FakeCollection cannot apply {op}")
            for path, value in fields.items():
                operator(doc, path, value)

    async def update_one(
        self,
        filter_: Mapping[str, Any],
        update: Mapping[str, Any],
        upsert: bool = False,
        session: object = None,
    ) -> FakeUpdateResult:
        self.sessions.append(session)
        matches = self._all(filter_)
        if matches:
            self._apply_update(matches[0], update)
            return FakeUpdateResult(matched_count=1)
        if not upsert:
            return FakeUpdateResult(matched_count=0)
        created: dict[str, Any] = {
            k: copy.deepcopy(v) for k, v in filter_.items() if "." not in k
        }
        created.update(copy.deepcopy(dict(update.get("$setOnInsert", {}))))
        self._check_unique(created)
        self._apply_update(created, update)
        self.docs.append(created)
        return FakeUpdateResult(matched_count=0, upserted_id=created.get("_id", "new"))

    async def find_one_and_update(
        self,
        filter_: Mapping[str, Any],
        update: Mapping[str, Any],
        sort: Optional[Sequence[tuple[str, int]]] = None,
        upsert: bool = False,
        return_document: object = None,
        projection: Optional[Mapping[str, int]] = None,
        session: object = None,
    ) -> Optional[dict[str, Any]]:
        self.sessions.append(session)
        matches = self._all(filter_)
        if not matches:
            return None
        self._apply_update(matches[0], update)
        return copy.deepcopy(matches[0])

    async def count_documents(
        self, filter_: Optional[Mapping[str, Any]] = None, session: object = None
    ) -> int:
        return len(self._all(filter_))

    async def create_indexes(self, indexes: Sequence[Any]) -> list[str]:
        self.indexes.extend(indexes)
        return [str(i.document["name"]) for i in indexes]


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
    agent-worker test suite's own ``recording_audit`` fixture), and the
    ``Idempotency-Key`` it was sent with.
    """

    def __init__(self) -> None:
        self.records: list[RecordAppend] = []
        self.keys: list[Optional[str]] = []

    async def append(
        self, record: RecordAppend, *, idempotency_key: Optional[str] = None
    ) -> AuditRecord:
        self.records.append(record)
        self.keys.append(idempotency_key)
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


class FakeTransact:
    """Stands in for :class:`fcrm_caseflow.engine.Transact`: runs ``fn`` once
    with a fresh :class:`FakeSession`. There is no commit or abort: what a
    real replica set does with the writes is ``test_engine_live.py``'s job.
    """

    def __init__(self) -> None:
        self.sessions: list[FakeSession] = []

    async def __call__(self, fn: Callable[[Any], Awaitable[R]]) -> R:
        session = FakeSession(in_transaction=True)
        self.sessions.append(session)
        return await fn(session)


@pytest.fixture
def case_runs_collection() -> FakeCollection:
    return FakeCollection("case_runs")


@pytest.fixture
def run_events_collection() -> FakeCollection:
    return FakeCollection(
        "run_events", unique=[("run_id", "seq"), ("run_id", "kind", "data.step_id")]
    )


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
def transact() -> FakeTransact:
    return FakeTransact()


@pytest.fixture
def engine_factory(
    run_store: CaseRunStore,
    gate_store: GateStore,
    outbox: Outbox,
    recording_audit: RecordingAudit,
    transact: FakeTransact,
) -> Callable[..., CaseflowEngine]:
    """Builds an engine over the fakes; ``step_map`` injects trigger -> step
    entries (the shipped ``STEP_FOR`` is empty).
    """

    def build(
        step_map: Optional[Mapping[Trigger, StepKind]] = None,
    ) -> CaseflowEngine:
        kwargs: dict[str, Any] = {"transact": transact}
        if step_map is not None:
            kwargs["step_map"] = step_map
        return CaseflowEngine(
            run_store,
            gate_store,
            outbox,
            recording_audit,
            **kwargs,
        )

    return build


@pytest.fixture
def engine(engine_factory: Callable[..., CaseflowEngine]) -> CaseflowEngine:
    return engine_factory()


@pytest.fixture
def session() -> FakeSession:
    return FakeSession(in_transaction=True)
