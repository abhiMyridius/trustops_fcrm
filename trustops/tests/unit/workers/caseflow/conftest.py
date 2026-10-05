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
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional, TypeVar
from urllib.parse import unquote

import httpx
import pytest
from fcrm_caseflow.audit_client import AuditClient
from fcrm_caseflow.clients.case_client import CaseClient
from fcrm_caseflow.clients.decision_votes_client import DecisionVotesClient
from fcrm_caseflow.clients.policy_client import PolicyClient
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.gates import GateStore
from fcrm_caseflow.steps import StepDeps
from fcrm_caseflow.store import (
    DEFAULT_PINS,
    AgentResultStore,
    CaseRun,
    CaseRunStore,
    case_run_from_document,
    new_case_run_document,
)
from fcrm_caseflow.triggers import Trigger
from fcrm_common.http import ServiceClient
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.api.audit import AuditRecord, RecordAppend, VerifyResult
from fcrm_contracts.enums import CaseState, StepKind
from fcrm_contracts.events.envelope import Envelope
from pymongo.errors import DuplicateKeyError

R = TypeVar("R")


def document_stored_payload(doc_id: str = "doc_1", **overrides: Any) -> dict[str, Any]:
    """A full ``case.document_stored`` payload, as S1's contract promises it
    (``fcrm_contracts.events.payloads.CaseDocumentStored``). The engine reads
    ``bucket`` and ``key`` from it to hand the document to H3.
    """
    return {
        "case_id": "FCRM-2026-0151",
        "actor": "u-pnair",
        "doc_id": doc_id,
        "name": f"{doc_id}.pdf",
        "bucket": "uploads",
        "key": f"FCRM-2026-0151/{doc_id}/{doc_id}.pdf",
        "sha256": "0" * 64,
        "size_bytes": 1024,
        "content_type": "application/pdf",
        **overrides,
    }


def doc_source(doc: str = "doc_1", page: int = 1, line: int = 3) -> dict[str, Any]:
    """A ``DocSourceRef`` as an agent's claim carries it."""
    return {
        "kind": "doc",
        "doc_id": doc,
        "page": page,
        "line_start": line,
        "line_end": line,
    }


def extracted_field(
    field_id: str,
    value: str,
    confidence: float = 0.9,
    *,
    label: Optional[str] = None,
    source: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """One ``ExtractedField`` the intake agent proposed."""
    return {
        "field_id": field_id,
        "label": label or f"Label {field_id}",
        "value": value,
        "source_ref": source or doc_source(),
        "quote": f"quote for {value}",
        "confidence": confidence,
    }


def extraction_proposal(*fields: dict[str, Any]) -> dict[str, Any]:
    """An ``ExtractionProposal`` (one document window) of ``fields``."""
    return {"case_id": "FCRM-2026-0151", "window_id": "w1", "fields": list(fields)}


def category_proposal(
    change_type: str, confidence: float = 0.9, source: Optional[dict[str, Any]] = None
) -> dict[str, Any]:
    """A ``CategoryProposal`` the intake agent proposed."""
    return {
        "change_type": change_type,
        "source_ref": source or doc_source(),
        "quote": f"quote for {change_type}",
        "confidence": confidence,
    }


def gap_question(
    question_id: str, field_id: Optional[str] = "F10", blocking: bool = True
) -> dict[str, Any]:
    """A ``GapQuestion`` the intake agent asked."""
    return {
        "question_id": question_id,
        "field_id": field_id,
        "text": f"What is {field_id}?",
        "blocking": blocking,
    }


def fact_flag(text: str, field_id: Optional[str] = "F21") -> dict[str, Any]:
    """A ``FactFlag`` the intake agent raised."""
    return {
        "source_ref": doc_source(),
        "quote": "quote",
        "confidence": 0.7,
        "field_id": field_id,
        "text": text,
        "kind": "new_fact",
    }


def agent_done_body(
    job_id: str,
    run_id: str,
    *accepted: dict[str, Any],
    agent: str = "intake",
) -> dict[str, Any]:
    """An ``agent.done`` body as H2 publishes it, carrying ``accepted``."""
    return {
        "job_id": job_id,
        "case_id": "FCRM-2026-0151",
        "run_id": run_id,
        "agent": agent,
        "output": {
            "accepted": list(accepted),
            "dropped": [],
            "notes_for_analyst": [],
            "guard_summary": {"claims_total": 0, "claims_with_source": 0, "dropped": 0},
            "attempts": 1,
        },
        "usage": {"input_tokens": 1200, "output_tokens": 300},
        "model": "anthropic:claude-haiku",
        "attempt": 1,
    }


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
        self.verify_ok = True
        self.first_break_seq: Optional[int] = None
        self.verified: list[str] = []

    async def verify(self, case_id: str) -> VerifyResult:
        """What S4's ``GET /verify`` would say: the chain is intact unless
        ``verify_ok`` was set False. The count is the records appended so far.
        """
        self.verified.append(case_id)
        return VerifyResult(
            case_id=case_id,
            record_count=len(self.records),
            ok=self.verify_ok,
            first_break_seq=self.first_break_seq,
        )

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
    entries. Without one the engine maps no trigger to a step (not the shipped
    ``STEP_FOR``, which the intake core fills), so a test that is not about
    steps sees only the transitions it drives.
    """

    def build(
        step_map: Optional[Mapping[Trigger, StepKind]] = None,
        **overrides: Any,
    ) -> CaseflowEngine:
        kwargs: dict[str, Any] = {
            "transact": transact,
            "step_map": step_map if step_map is not None else {},
            **overrides,
        }
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


# --- the peer services H1's steps call ----------------------------------------


class Call(NamedTuple):
    """One request a fake peer received: ``path`` is relative to its base URL."""

    method: str
    path: str
    body: Any
    headers: Mapping[str, str]
    params: Mapping[str, str]


async def _no_sleep(_seconds: float) -> None:
    """Stands in for ``asyncio.sleep`` so retry backoff costs no time."""


def problem(status: int, detail: str = "boom") -> httpx.Response:
    """An ``application/problem+json`` error response, as the services send."""
    return httpx.Response(
        status,
        json={
            "type": "about:blank",
            "title": "error",
            "status": status,
            "detail": detail,
        },
        headers={"content-type": "application/problem+json"},
    )


class FakePeer:
    """The shared half of the fake S1 and S3: a request log, queued failures
    and an ``httpx.MockTransport``-ready handler.

    Subclasses implement :meth:`route`. A response queued with :meth:`fail` is
    returned once, for the first matching request, instead of the real answer.
    """

    BASE_PATH = ""

    def __init__(self) -> None:
        self.calls: list[Call] = []
        self._failures: list[tuple[str, str, httpx.Response]] = []

    def fail(self, method: str, path_contains: str, response: httpx.Response) -> None:
        """Answer the next ``method`` request whose path contains
        ``path_contains`` with ``response``.
        """
        self._failures.append((method, path_contains, response))

    def calls_to(self, method: str, path_contains: str = "") -> list[Call]:
        """The requests of ``method`` whose path contains ``path_contains``."""
        return [c for c in self.calls if c.method == method and path_contains in c.path]

    def handler(self, request: httpx.Request) -> httpx.Response:
        """The ``httpx.MockTransport`` handler."""
        raw_path = request.url.raw_path.decode("ascii").split("?")[0]
        path = raw_path.removeprefix(self.BASE_PATH)
        body = json.loads(request.content) if request.content else None
        call = Call(
            request.method,
            path,
            body,
            dict(request.headers),
            dict(request.url.params),
        )
        self.calls.append(call)
        for index, (method, contains, response) in enumerate(self._failures):
            if method == call.method and contains in call.path:
                del self._failures[index]
                return response
        return self.route(call)

    def route(self, call: Call) -> httpx.Response:
        raise NotImplementedError

    def client(self, dependency: str) -> ServiceClient:
        """A real :class:`ServiceClient` whose transport is this fake."""
        return ServiceClient(
            f"http://{dependency}.test{self.BASE_PATH}",
            dependency=dependency,
            transport=httpx.MockTransport(self.handler),
            sleep=_no_sleep,
        )


class FakeCaseService(FakePeer):
    """A stateful stand-in for S1's internal routes (``/internal/...``).

    Holds one case (the demo's) plus any ``add_precedent`` cases, its
    documents, a roster of analysts with open-case counts, and logs what H1
    pushed: ``intake_pushes``, ``events``, ``state_patches``, ``assignments``.
    """

    BASE_PATH = "/api/fcrm"

    def __init__(self, case_id: str = "FCRM-2026-0151") -> None:
        super().__init__()
        self.case_id = case_id
        self.stage = "intake"
        self.run_id: Optional[str] = None
        self.owner = {"id": "u-pnair", "name": "Priya Nair"}
        self.analyst: Optional[dict[str, str]] = None
        self.roster: dict[str, tuple[str, int]] = {
            "u-riyer": ("Rahul Iyer", 4),
            "u-mshah": ("Meera Shah", 5),
            "u-dbrooks": ("Daniel Brooks", 6),
        }
        self.change_types: list[str] = []
        self.required_field_ids: list[str] = []
        self.fields: list[dict[str, Any]] = []
        self.documents: dict[str, dict[str, Any]] = {}
        self.page_maps: dict[str, Any] = {}
        self.other_cases: dict[str, dict[str, Any]] = {}
        self.intake_pushes: list[dict[str, Any]] = []
        self.events: list[dict[str, Any]] = []
        self.state_patches: list[dict[str, Any]] = []
        self.assignments: list[dict[str, Any]] = []

    # -- seeding ----------------------------------------------------------

    def add_document(
        self, doc_id: str, name: str, status: str = "stored", **extra: Any
    ) -> None:
        """A document the PO uploaded."""
        self.documents[doc_id] = {
            "doc_id": doc_id,
            "case_id": self.case_id,
            "name": name,
            "status": status,
            "bucket": "uploads",
            "key": f"{self.case_id}/{doc_id}/{name}",
            "size_bytes": 1024,
            "content_type": "application/pdf",
            "uploaded_at": "2026-10-05T10:00:00Z",
            **extra,
        }

    def add_precedent(
        self,
        case_id: str,
        title: str,
        values: Mapping[str, tuple[str, str]],
        unit: str = "Payments",
    ) -> None:
        """A closed case whose fields (``{field_id: (label, value)}``) the PO
        may reuse.
        """
        record = self._record(case_id)
        record.update(
            {
                "title": title,
                "unit": unit,
                "state": "closed",
                "analyst": {"id": "u-mshah", "name": "Meera Shah"},
                "documents": [],
                "fields": [
                    {
                        "field_id": field_id,
                        "label": label,
                        "value": value,
                        "origin": "owner_declared",
                    }
                    for field_id, (label, value) in values.items()
                ],
            }
        )
        self.other_cases[case_id] = record

    # -- the routes -------------------------------------------------------

    def _record(self, case_id: str) -> dict[str, Any]:
        return {
            "case_id": case_id,
            "title": "Consumer remittances US → Mexico via payout partner",
            "unit": "Payments",
            "owner": self.owner,
            "analyst": self.analyst,
            "state": self.stage,
            "run_id": self.run_id,
            "change_types": list(self.change_types),
            "domains": [],
            "pins": DEFAULT_PINS.model_dump(mode="json"),
            "fieldset_v": 6,
            "required_field_ids": list(self.required_field_ids),
            "fields": [dict(f) for f in self.fields],
            "documents": [dict(d) for d in self.documents.values()],
            "submission_version": None,
        }

    def route(self, call: Call) -> httpx.Response:
        parts = [unquote(p) for p in call.path.strip("/").split("/")]
        if parts[:2] == ["internal", "cases"] and len(parts) == 4:
            return self._case_route(call, parts[2], parts[3])
        if parts[:2] == ["internal", "documents"] and len(parts) == 3:
            return self._document_route(call, parts[2])
        if parts == ["internal", "analysts", "load"]:
            loads = [
                {"analyst_id": a, "open_case_count": n}
                for a, (_, n) in sorted(self.roster.items())
            ]
            return httpx.Response(200, json={"loads": loads})
        return problem(404, f"no route {call.method} {call.path}")

    def _case_route(self, call: Call, case_id: str, leaf: str) -> httpx.Response:
        if leaf == "record":
            if case_id in self.other_cases:
                return httpx.Response(200, json=self.other_cases[case_id])
            if case_id != self.case_id:
                return problem(404, f"no case {case_id}")
            return httpx.Response(200, json=self._record(case_id))
        if (call.method, leaf) == ("PUT", "intake"):
            self.intake_pushes.append(call.body)
            return httpx.Response(200, json={"ok": True})
        if (call.method, leaf) == ("PATCH", "state"):
            self.state_patches.append(call.body)
            self.stage = call.body["stage"]
            self.run_id = call.body.get("run_id", self.run_id)
            return httpx.Response(200, json={"ok": True})
        if (call.method, leaf) == ("POST", "assign"):
            self.assignments.append(call.body)
            name, count = self.roster[call.body["analyst_id"]]
            self.analyst = {"id": call.body["analyst_id"], "name": name}
            self.roster[call.body["analyst_id"]] = (name, count + 1)
            return httpx.Response(200, json={"ok": True})
        if (call.method, leaf) == ("POST", "events"):
            self.events.append(call.body)
            return httpx.Response(202, json={"ok": True})
        return problem(404, f"no route {call.method} {call.path}")

    def _document_route(self, call: Call, doc_id: str) -> httpx.Response:
        document = self.documents.get(doc_id)
        if document is None:
            return problem(404, f"no document {doc_id}")
        if call.method == "PATCH":
            body = call.body
            document.update(
                {k: v for k, v in body.items() if k != "page_map" and v is not None}
            )
            self.page_maps[doc_id] = body.get("page_map")
        return httpx.Response(200, json=document)


class FakePolicyService(FakePeer):
    """A stand-in for S3's ``POST /search``: answers with ``hits`` verbatim."""

    BASE_PATH = "/api/fcrm/policy"

    def __init__(self) -> None:
        super().__init__()
        self.hits: list[dict[str, Any]] = []

    def route(self, call: Call) -> httpx.Response:
        if (call.method, call.path) == ("POST", "/search"):
            return httpx.Response(200, json={"hits": self.hits})
        return problem(404, f"no route {call.method} {call.path}")


@pytest.fixture
def case_service() -> FakeCaseService:
    return FakeCaseService()


@pytest.fixture
def policy_service() -> FakePolicyService:
    return FakePolicyService()


@pytest.fixture
def case_client(case_service: FakeCaseService) -> CaseClient:
    return CaseClient(case_service.client("case"))


@pytest.fixture
def policy_client(policy_service: FakePolicyService) -> PolicyClient:
    return PolicyClient(policy_service.client("policy"))


@pytest.fixture
def agent_results_collection() -> FakeCollection:
    return FakeCollection("agent_results")


@pytest.fixture
def agent_results(agent_results_collection: FakeCollection) -> AgentResultStore:
    return AgentResultStore(agent_results_collection)  # type: ignore[arg-type]


def make_run(
    *,
    run_id: str = "run_1",
    case_id: str = "FCRM-2026-0151",
    state: CaseState = CaseState.INTAKE,
    refs: Optional[Mapping[str, Any]] = None,
) -> CaseRun:
    """A :class:`CaseRun` as a handler sees it (the snapshot taken when its
    step began), in ``state`` with ``refs``.
    """
    document = new_case_run_document(run_id=run_id, case_id=case_id, pins=DEFAULT_PINS)
    document["state"] = state.value
    document["refs"] = dict(refs or {})
    return case_run_from_document(document)


@pytest.fixture
def step_deps_factory(
    recording_audit: RecordingAudit,
    case_client: CaseClient,
    policy_client: PolicyClient,
    agent_results: AgentResultStore,
) -> Callable[..., StepDeps]:
    """Builds the :class:`StepDeps` a handler gets, over the fakes."""

    def build(run: Optional[CaseRun] = None, **overrides: Any) -> StepDeps:
        values: dict[str, Any] = {
            "run": run if run is not None else make_run(),
            "audit": recording_audit,
            "case": case_client,
            "policy": policy_client,
            "agent_results": agent_results,
            **overrides,
        }
        return StepDeps(**values)

    return build


# --- the decision core's peers (S5 and S1's outcome route) --------------------------

DECISION_BASE = "https://trustops.local/errors/"

#: The demo's three conditions as the pack's narrative drafted them (the seed
#: extraction's table, 06-walkthrough-seed-extraction.md section 3.8).
DEMO_CONDITIONS: list[dict[str, Any]] = [
    {
        "id": "CND-1",
        "text": "Corridor-scope vendor DD review completed and filed",
        "owner": "Priya Nair",
        "due": "before go-live",
        "status": "open",
        "from": "overridden control CTL-T01",
    },
    {
        "id": "CND-2",
        "text": "Enhanced monitoring rules on payout agents live",
        "owner": "FCRM Ops",
        "due": "go-live",
        "status": "scheduled",
        "from": "planned control",
    },
    {
        "id": "CND-3",
        "text": "Quarterly sample review of agent KYC files",
        "owner": "Priya Nair",
        "due": "6 months",
        "status": "scheduled",
        "from": "policy gap (cash-pickup agents)",
    },
]

COMMITTEE = ("u-amendes", "u-jokafor", "u-lchen")


def coded_problem(status: int, code: str, detail: str = "refused") -> httpx.Response:
    """An error response whose problem ``type`` ends in ``code``, as S5 sends."""
    return httpx.Response(
        status,
        json={
            "type": f"{DECISION_BASE}{code}",
            "title": code,
            "status": status,
            "detail": detail,
        },
        headers={"content-type": "application/problem+json"},
    )


class FakeDecisionService(FakePeer):
    """A stand-in for S5's service surface: ``POST /outcomes`` (refusing what S5
    refuses: an untallied pack, a different outcome, a delegated close over an
    open committee pack), ``POST .../supersede``, ``POST .../revote`` and the
    pack and outcome reads.

    ``tallied`` is the outcome S5 would have tallied for the pack (``None`` =
    nothing tallied yet); ``committee_pending`` makes a delegated outcome
    refuse. A repeated ``Idempotency-Key`` answers what the first did.
    """

    BASE_PATH = "/api/fcrm/decision"

    def __init__(self, pack: dict[str, Any]) -> None:
        super().__init__()
        self.pack = pack
        self.tallied: Optional[str] = None
        self.committee_pending = False
        self.revote_round = 2
        self.voided_votes = 2
        self.decided_at = "2026-10-05T14:00:00Z"
        self.outcomes: list[dict[str, Any]] = []
        self.supersedes: list[dict[str, Any]] = []
        self.revotes: list[dict[str, Any]] = []
        self.case_outcome: Optional[dict[str, Any]] = None
        self._answers: dict[str, httpx.Response] = {}

    def route(self, call: Call) -> httpx.Response:
        parts = [unquote(p) for p in call.path.strip("/").split("/")]
        key = call.headers.get("idempotency-key")
        if key is not None and key in self._answers:
            return self._answers[key]
        response = self._route(call, parts)
        if key is not None and response.status_code < 300:
            self._answers[key] = response
        return response

    def _route(self, call: Call, parts: list[str]) -> httpx.Response:
        if (call.method, parts) == ("POST", ["outcomes"]):
            return self._outcome(call)
        if call.method == "POST" and len(parts) == 3 and parts[0] == "packs":
            if parts[2] == "supersede":
                self.supersedes.append({"pack_id": parts[1], **(call.body or {})})
                return httpx.Response(
                    200,
                    json={
                        "pack_id": parts[1],
                        "version": 1,
                        "status": "superseded",
                        "voided_votes": self.voided_votes,
                        "rerequested": list(COMMITTEE[: self.voided_votes]),
                    },
                )
            if parts[2] == "revote":
                self.revotes.append({"pack_id": parts[1], **(call.body or {})})
                return httpx.Response(
                    200,
                    json={
                        "pack_id": parts[1],
                        "round": self.revote_round,
                        "member_ids": list(COMMITTEE),
                    },
                )
        if call.method == "GET" and parts[:1] == ["packs"] and len(parts) == 2:
            return httpx.Response(200, json=self.pack)
        if call.method == "GET" and parts[:1] == ["cases"]:
            if parts[-2:] == ["packs", "latest"]:
                return httpx.Response(200, json=self.pack)
            if parts[-1:] == ["outcome"]:
                if self.case_outcome is None:
                    return problem(404, "no outcome yet")
                return httpx.Response(200, json=self.case_outcome)
        return problem(404, f"no route {call.method} {call.path}")

    def _outcome(self, call: Call) -> httpx.Response:
        body = call.body or {}
        if body["mode"] == "committee":
            if self.tallied is None:
                return coded_problem(409, "not_tallied")
            if body["outcome"] != self.tallied:
                return coded_problem(409, "outcome_mismatch")
        elif self.committee_pending:
            return coded_problem(409, "committee_pending")
        self.outcomes.append(body)
        return httpx.Response(
            200,
            json={
                "case_id": body["case_id"],
                "outcome": body["outcome"],
                "label": body["outcome"].replace("_", " ").title(),
                "mode": body["mode"],
                "pack_id": body.get("pack_id"),
                "round": 1,
                "decided_at": self.decided_at,
                "decided_by": body.get("decided_by"),
                "reason": body.get("reason"),
                "plain_summary": body.get("plain_summary"),
                "conditions": body.get("conditions", []),
                "recorded": True,
            },
        )


class FakeClosingCaseService(FakeCaseService):
    """S1's internal routes plus ``PUT /internal/cases/{id}/outcome``: closes
    the fake case, remembers every body and key, and refuses a different body
    under a known key or for a decided case, as S1 does.
    """

    def __init__(self, case_id: str = "FCRM-2026-0151") -> None:
        super().__init__(case_id)
        self.analyst = {"id": "u-riyer", "name": "Rahul Iyer"}
        self.fields = [
            {
                "field_id": "F03",
                "label": "Go-live date",
                "value": "February 2027",
                "origin": "owner_declared",
            }
        ]
        self.outcome_puts: list[dict[str, Any]] = []
        self.outcome_keys: dict[str, dict[str, Any]] = {}

    def _case_route(self, call: Call, case_id: str, leaf: str) -> httpx.Response:
        if (call.method, leaf) != ("PUT", "outcome"):
            return super()._case_route(call, case_id, leaf)
        key = call.headers.get("idempotency-key", "")
        body = call.body or {}
        if key in self.outcome_keys:
            if self.outcome_keys[key] != body:
                return coded_problem(409, "idempotency_key_reused")
            return httpx.Response(200, json=self._outcome_result(body, replayed=True))
        if self.outcome_puts and self.outcome_puts[-1] != body:
            return coded_problem(409, "outcome_already_set")
        self.outcome_keys[key] = body
        if not self.outcome_puts:
            self.outcome_puts.append(body)
        self.stage = "closed"
        return httpx.Response(200, json=self._outcome_result(body, replayed=False))

    @staticmethod
    def _outcome_result(body: dict[str, Any], *, replayed: bool) -> dict[str, Any]:
        return {
            "case_id": "FCRM-2026-0151",
            "outcome": body["outcome"],
            "mode": body["mode"],
            "closed": True,
            "conditions_activated": len(body.get("conditions", [])),
            "replayed": replayed,
        }


@pytest.fixture
def closing_case_service() -> FakeClosingCaseService:
    return FakeClosingCaseService()


@pytest.fixture
def decision_service(
    release_snapshot: Callable[..., dict[str, Any]],
) -> FakeDecisionService:
    """The fake S5 holding the demo's released pack v1 with its three drafted
    conditions and a business summary the PO may read.
    """
    snapshot = release_snapshot("FCRM-2026-0151")
    body = {k: v for k, v in snapshot.items() if k not in ("checks", "change_types")}
    body.update(
        {
            "pack_id": "pack_1",
            "version": 1,
            "content_hash": "ab" * 32,
            "released_at": "2026-10-05T12:00:00Z",
            "status": "released",
            "addenda": [],
            "conditions": DEMO_CONDITIONS,
            "business_summary": (
                "The cross-border remittance service can launch once the payout "
                "partner's review for this corridor is on file."
            ),
        }
    )
    return FakeDecisionService(body)


@pytest.fixture
def decision_votes_client(decision_service: FakeDecisionService) -> DecisionVotesClient:
    return DecisionVotesClient(decision_service.client("decision"))


@pytest.fixture
def closing_case_client(closing_case_service: FakeClosingCaseService) -> CaseClient:
    return CaseClient(closing_case_service.client("case"))


@pytest.fixture
def decision_deps_factory(
    recording_audit: RecordingAudit,
    closing_case_client: CaseClient,
    decision_votes_client: DecisionVotesClient,
) -> Callable[..., StepDeps]:
    """Builds the :class:`StepDeps` a decision-core handler gets, over the fakes."""

    def build(run: Optional[CaseRun] = None, **overrides: Any) -> StepDeps:
        values: dict[str, Any] = {
            "run": run if run is not None else make_run(state=CaseState.DECIDE),
            "audit": recording_audit,
            "case": closing_case_client,
            "decision_votes": decision_votes_client,
            **overrides,
        }
        return StepDeps(**values)

    return build
