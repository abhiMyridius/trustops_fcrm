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
import hashlib
import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, NamedTuple, Optional, TypeVar
from urllib.parse import unquote

import httpx
import pytest
from fcrm_case.domain.assessment import build_score_view
from fcrm_case.domain.views import DecisionRow, ReviewItemRow
from fcrm_caseflow.audit_client import AuditClient
from fcrm_caseflow.clients.case_client import CaseClient
from fcrm_caseflow.clients.decision_client import DecisionClient
from fcrm_caseflow.clients.decision_votes_client import DecisionVotesClient
from fcrm_caseflow.clients.policy_client import PolicyClient
from fcrm_caseflow.clients.scoring_client import ScoringClient
from fcrm_caseflow.cores import assessment
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.gates import GateStore
from fcrm_caseflow.steps import StepDeps, StepResult
from fcrm_caseflow.store import (
    DEFAULT_PINS,
    AgentResultStore,
    CaseRun,
    CaseRunStore,
    case_run_from_document,
    new_case_run_document,
)
from fcrm_caseflow.triggers import Trigger
from fcrm_common.errors import ConflictError
from fcrm_common.http import ServiceClient
from fcrm_common.outbox import Outbox
from fcrm_common.time import utcnow
from fcrm_contracts.api.audit import (
    AuditRecord,
    Proposal,
    ProposalAppend,
    RecordAppend,
    VerifyResult,
)
from fcrm_contracts.api.decision import PackContent
from fcrm_contracts.api.scoring import ScoreRequest
from fcrm_contracts.enums import CaseState, ItemKind, ItemStatus, StepKind
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep
from fcrm_scoring.engine import compute_score
from fcrm_scoring.seedmodel import scoring_model_from_seed
from fcrm_seed.loader import control_catalogue_weights, load_raw
from pydantic import ValidationError
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


def clause_source(clause_id: str, corpus_v: int = 14) -> dict[str, Any]:
    """A ``ClauseSourceRef`` as a research claim carries it."""
    return {"kind": "clause", "clause_id": clause_id, "corpus_v": corpus_v}


def clause_proposal(
    clause_id: str, domain: str, quote: Optional[str] = None, confidence: float = 0.9
) -> dict[str, Any]:
    """A research ``ClauseProposal``."""
    return {
        "source_ref": clause_source(clause_id),
        "quote": quote or f"verbatim text of {clause_id}",
        "confidence": confidence,
        "clause_id": clause_id,
        "domain": domain,
        "applicability": f"{clause_id} applies",
    }


def precedent_match(case_id: str, similarity: float = 0.8) -> dict[str, Any]:
    """A research ``PrecedentMatch``."""
    return {
        "case_id": case_id,
        "similarity": similarity,
        "outcome": "approve_with_conditions",
        "note": "same corridor",
    }


def policy_gap(domain: str, description: str) -> dict[str, Any]:
    """A research ``PolicyGap`` with the proof of the search."""
    return {
        "domain": domain,
        "description": description,
        "searched": {
            "query": "cash pickup agent",
            "indexes": ["policy_clauses"],
            "hits": 0,
        },
    }


def case_source(case_id: str = "FCRM-2026-0151") -> dict[str, Any]:
    """A ``CaseSourceRef``."""
    return {"kind": "case", "case_id": case_id}


def factor_proposal(
    domain: str, likelihood: int, impact: int, confidence: float = 0.85
) -> dict[str, Any]:
    """An assessment ``FactorProposal``."""
    return {
        "source_ref": case_source(),
        "quote": f"{domain} facts",
        "confidence": confidence,
        "domain": domain,
        "likelihood": likelihood,
        "impact": impact,
        "justification": f"{domain} likelihood {likelihood}, impact {impact}",
    }


def control_proposal(
    control_id: str,
    domain: str,
    rating: Optional[float],
    *,
    evidence: str = "evidence artefact",
    evidence_date: str = "2026-05-01",
    planned: bool = False,
    confidence: float = 0.8,
) -> dict[str, Any]:
    """An assessment ``ControlProposal`` (``rating`` is ``None`` when planned)."""
    return {
        "source_ref": {
            "kind": "control",
            "control_id": control_id,
            "controls_v": 9,
        },
        "quote": f"evidence for {control_id}",
        "confidence": confidence,
        "control_id": control_id,
        "domain": domain,
        "rating": rating,
        "rationale": f"{control_id} rationale",
        "evidence_artifact": evidence,
        "evidence_date": evidence_date,
        "planned": planned,
    }


def record_source(seq: int, case_id: str = "FCRM-2026-0151") -> dict[str, Any]:
    """A ``RecordSourceRef``."""
    return {"kind": "record", "case_id": case_id, "seq": seq}


def critic_finding(
    text: str,
    finding_type: str = "precedent_deviation",
    severity: str = "high",
    seq: int = 30,
) -> dict[str, Any]:
    """A critic ``CriticFinding``."""
    return {
        "source_ref": record_source(seq),
        "quote": text[:20],
        "confidence": 0.9,
        "finding_type": finding_type,
        "severity": severity,
        "record_refs": [f"#{seq}"],
        "text": text,
    }


def completeness_report(
    missing: int = 0,
    stale: int = 1,
    contradictory: int = 1,
    drafted: int = 3,
    kept: int = 2,
) -> dict[str, Any]:
    """A narrative ``CompletenessReport``."""
    return {
        "missing": [f"missing {n}" for n in range(missing)],
        "stale": [f"stale {n}" for n in range(stale)],
        "contradictory": [f"contradiction {n}" for n in range(contradictory)],
        "questions_drafted": drafted,
        "questions_kept": kept,
    }


def drafted_question(
    question_id: str, text: str = "Is a review planned?"
) -> dict[str, Any]:
    """A narrative ``Question``."""
    return {"question_id": question_id, "anchored_to": "CTL-T01", "text": text}


def narrative_text(text: str, seq: int = 32) -> dict[str, Any]:
    """A narrative ``Narrative`` (the recommendation text)."""
    return {
        "source_ref": record_source(seq),
        "quote": text[:20],
        "confidence": 0.9,
        "text": text,
    }


def drafted_condition(condition_id: str, text: str) -> dict[str, Any]:
    """A narrative ``Condition``."""
    return {
        "condition_id": condition_id,
        "text": text,
        "owner": "Priya Nair",
        "due": "before go-live",
        "status": "open",
        "source": "overridden control CTL-T01",
    }


def business_summary(text: str) -> dict[str, Any]:
    """A narrative ``BusinessSummary``."""
    return {"text": text, "restricted_term_hits": 0}


def screening_proposal(fast_lane: bool = False) -> dict[str, Any]:
    """A screening ``ScreeningProposal``."""
    return {
        "case_id": "FCRM-2026-0151",
        "fast_lane": fast_lane,
        "reasons": [
            {
                "source_ref": case_source(),
                "quote": "three change types",
                "confidence": 0.9,
                "text": "3 change types, new geography",
            }
        ],
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
        self.proposals: list[ProposalAppend] = []
        self.proposal_keys: list[Optional[str]] = []
        self._stored: dict[str, Proposal] = {}
        self._by_key: dict[str, AuditRecord] = {}
        self._chain_length = 0

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
        """Log the call, and answer as S4 does: a repeated ``Idempotency-Key``
        returns the record it already wrote (same ``seq`` and hash, nothing new
        chained); anything else is the chain's next record.
        """
        self.records.append(record)
        self.keys.append(idempotency_key)
        if idempotency_key is not None and idempotency_key in self._by_key:
            return self._by_key[idempotency_key]
        self._chain_length += 1
        stored = AuditRecord(
            case_id=record.case_id,
            seq=self._chain_length,
            type=record.type,
            actor=record.actor,
            summary=record.summary,
            data=record.data,
            prev_hash=f"{self._chain_length - 1:064x}",
            hash=f"{self._chain_length:064x}",
            created_at=utcnow(),
        )
        if idempotency_key is not None:
            self._by_key[idempotency_key] = stored
        return stored

    async def append_proposal(
        self,
        proposal: ProposalAppend,
        *,
        idempotency_key: Optional[str] = None,
    ) -> Proposal:
        """Record the proposal and answer as S4 does: a repeated key returns
        the entry already stored (the same id), a new one gets the next ``seq``.
        """
        self.proposals.append(proposal)
        self.proposal_keys.append(idempotency_key)
        if idempotency_key is not None and idempotency_key in self._stored:
            return self._stored[idempotency_key]
        number = len(self._stored) + 1
        stored = Proposal(
            **proposal.model_dump(),
            proposal_id=f"prp_{number:04d}",
            seq=number,
            created_at=utcnow(),
        )
        self._stored[idempotency_key or f"anon{number}"] = stored
        return stored


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


#: The ``PUT /internal/cases/{id}/<leaf>`` view writes of the assessment half.
VIEW_LEAVES = (
    "screening",
    "score",
    "critic",
    "completeness",
    "recommendation-draft",
    "handoff",
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
        # the assessment half
        self.domains: list[str] = []
        self.submission_version: Optional[int] = None
        self.items: dict[str, dict[str, Any]] = {}
        self.views: dict[str, list[dict[str, Any]]] = {name: [] for name in VIEW_LEAVES}
        self.write_keys: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        self.recommendation_edits: Optional[dict[str, Any]] = None
        self.locked = False
        self._item_counter = 0

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
            "domains": list(self.domains),
            "pins": DEFAULT_PINS.model_dump(mode="json"),
            "fieldset_v": 6,
            "required_field_ids": list(self.required_field_ids),
            "fields": [dict(f) for f in self.fields],
            "documents": [dict(d) for d in self.documents.values()],
            "submission_version": self.submission_version,
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
        return self._assessment_route(call, case_id, leaf)

    # -- the assessment half's routes ----------------------------------------

    def _assessment_route(self, call: Call, case_id: str, leaf: str) -> httpx.Response:
        if case_id != self.case_id:
            return problem(404, f"no case {case_id}")
        if leaf == "items":
            return self._items_route(call)
        if call.method == "PUT" and leaf in VIEW_LEAVES:
            return self._put_view(call, leaf)
        if (call.method, leaf) == ("GET", "score-request"):
            return self._score_request()
        if (call.method, leaf) == ("GET", "recommendation"):
            return self._recommendation()
        return problem(404, f"no route {call.method} {call.path}")

    def _items_route(self, call: Call) -> httpx.Response:
        if call.method == "GET":
            kind = call.params.get("kind")
            rows = [
                {k: v for k, v in row.items() if k != "created_at"}
                for row in self.items.values()
                if kind is None or row["kind"] == kind
            ]
            return httpx.Response(200, json=rows)
        if call.method != "PUT":
            return problem(405, "no such method")
        for upsert in call.body["items"]:
            existing = self.items.get(upsert["item_id"])
            content = {
                "kind": upsert["kind"],
                "proposal_id": upsert.get("proposal_id"),
                "gate_id": upsert.get("gate_id"),
                "subject": upsert.get("subject", {}),
                "proposed": upsert.get("proposed", {}),
                "source_refs": upsert.get("source_refs", []),
                "input_fields": upsert.get("input_fields", []),
                "supersedes": upsert.get("supersedes"),
                "run_id": call.body["run_id"],
            }
            if existing is not None:
                existing.update(content)
                continue
            self._item_counter += 1
            self.items[upsert["item_id"]] = {
                "item_id": upsert["item_id"],
                "status": upsert.get("status", "proposed"),
                "decided": None,
                "created_at": self._item_counter,
                **content,
            }
        return httpx.Response(
            200,
            json={
                "upserted": len(call.body["items"]),
                "item_ids": [i["item_id"] for i in call.body["items"]],
            },
        )

    def decide_item(
        self,
        item_id: str,
        status: str,
        *,
        value: Optional[dict[str, Any]] = None,
        reason: Optional[str] = None,
        by: str = "u-riyer",
    ) -> None:
        """What S1 stores when the analyst decides an item (before it publishes
        ``gate.decided``): the new status and the decision on the item.
        """
        row = self.items[item_id]
        row["status"] = status
        row["decided"] = {
            "value": value,
            "reason": reason,
            "by": by,
            "at": "2026-10-05T12:00:00Z",
        }

    def item_rows(self) -> list[ReviewItemRow]:
        """The stored items as S1's own pure builder reads them."""
        from datetime import UTC, datetime, timedelta

        base = datetime(2026, 10, 5, 9, tzinfo=UTC)
        rows: list[ReviewItemRow] = []
        for row in self.items.values():
            decided = row["decided"]
            rows.append(
                ReviewItemRow(
                    item_id=row["item_id"],
                    case_id=self.case_id,
                    kind=ItemKind(row["kind"]),
                    status=ItemStatus(row["status"]),
                    proposal_id=row["proposal_id"],
                    gate_id=row["gate_id"],
                    subject=row["subject"],
                    proposed=row["proposed"],
                    source_refs=row["source_refs"],
                    input_fields=row["input_fields"],
                    supersedes=row["supersedes"],
                    run_id=row["run_id"],
                    decided=(
                        DecisionRow(
                            value=decided["value"],
                            reason=decided["reason"],
                            by=decided["by"],
                            at=datetime(2026, 10, 5, 12, tzinfo=UTC),
                        )
                        if decided
                        else None
                    ),
                    created_at=base + timedelta(seconds=row["created_at"]),
                )
            )
        return rows

    def _put_view(self, call: Call, leaf: str) -> httpx.Response:
        key = call.headers.get("idempotency-key")
        replayed = False
        if key is not None:
            known = self.write_keys.get((leaf, key))
            if known is not None:
                if known[0] != json.dumps(call.body, sort_keys=True):
                    return problem(409, "Idempotency-Key reused for another body")
                replayed = True
        if not replayed:
            self.views[leaf].append(call.body)
            if key is not None:
                self.write_keys[(leaf, key)] = (
                    json.dumps(call.body, sort_keys=True),
                    call.body,
                )
        name = {"recommendation-draft": "recommendation"}.get(leaf, leaf)
        return httpx.Response(
            200,
            json={
                "case_id": self.case_id,
                "view": name,
                "id": f"snap_{len(self.views[leaf])}" if leaf == "score" else None,
                "replayed": replayed,
            },
        )

    def _score_request(self) -> httpx.Response:
        try:
            view = build_score_view(
                self.case_id, DEFAULT_PINS.scoring_v, self.item_rows()
            )
        except ConflictError as exc:
            return problem(409, str(exc))
        return httpx.Response(200, json=view.model_dump(mode="json"))

    def _recommendation(self) -> httpx.Response:
        drafts = self.views["recommendation-draft"]
        if not drafts:
            return problem(404, "no recommendation draft yet")
        body = dict(drafts[-1])
        return httpx.Response(
            200,
            json={
                "case_id": self.case_id,
                "run_id": body.pop("run_id"),
                "draft": body,
                "draft_seq": len(drafts),
                "edits": self.recommendation_edits,
                "locked": self.locked,
                "locked_by": None,
            },
        )

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
        if (call.method, call.path) == ("GET", "/controls"):
            return httpx.Response(200, json=self._inventory())
        if call.method == "GET" and call.path.startswith("/field-sets/"):
            return httpx.Response(200, json=self._field_set())
        return problem(404, f"no route {call.method} {call.path}")

    @staticmethod
    def _inventory() -> dict[str, Any]:
        """The seed's control inventory (v9), as S3 serves it."""
        weights = control_catalogue_weights()
        return {
            "version": "v9",
            "active": True,
            "controls": [
                {
                    "control_id": c["id"],
                    "domain": c["domain"],
                    "name": c["name"],
                    "catalogue_weight": weights[c["id"]],
                    "evidence_artifact": c["evidence_artefact"],
                    "last_tested": c["last_tested"],
                }
                for c in load_raw()["controls"]
            ],
        }

    @staticmethod
    def _field_set() -> dict[str, Any]:
        """The seed's field set (v6), as S3 serves it."""
        raw = load_raw()["field_set"]

        def field(entry: dict[str, Any], always: bool) -> dict[str, Any]:
            return {
                "field_id": entry["id"],
                "label": entry["label"],
                "always_required": always,
            }

        return {
            "version": "v6",
            "active": True,
            "max_required": raw["max_required"],
            "completion_threshold": raw["completion_threshold"],
            "always_required": [field(f, True) for f in raw["always_required"]],
            "per_type": {
                change_type: [field(f, False) for f in fields]
                for change_type, fields in raw["per_type"].items()
            },
        }


class FakeScoringService(FakePeer):
    """S2 as the real engine: ``POST /score`` returns what
    :func:`fcrm_scoring.engine.compute_score` computes for the request, and
    ``requests`` keeps every body received (so a test can assert on exactly
    what H1 sent, planned controls included or not). ``GET /models/{v}`` serves
    the seed's scoring model.
    """

    BASE_PATH = "/api/fcrm/scoring"

    def __init__(self) -> None:
        super().__init__()
        self.model = scoring_model_from_seed(load_raw()["scoring_model"])
        self.requests: list[dict[str, Any]] = []

    def route(self, call: Call) -> httpx.Response:
        if (call.method, call.path) == ("POST", "/score"):
            self.requests.append(call.body)
            request = ScoreRequest.model_validate(call.body)
            response = compute_score(request, self.model)
            return httpx.Response(200, json=response.model_dump(mode="json"))
        if call.method == "GET" and call.path.startswith("/models/"):
            return httpx.Response(200, json=self.model.model_dump(mode="json"))
        return problem(404, f"no route {call.method} {call.path}")


class FakeDecisionService(FakePeer):
    """S5's ``POST /packs`` (idempotent by content hash: the same snapshot gets
    the pack it already built, a changed one the next version) and
    ``POST /packs/{id}/supersede``. ``builds`` keeps every snapshot received.
    """

    BASE_PATH = "/api/fcrm/decision"
    CHECK_KEYS = (
        "locked",
        "business_summary_present",
        "all_items_reviewed",
        "boundary_and_band_acknowledged",
    )

    def __init__(self) -> None:
        super().__init__()
        self.builds: list[dict[str, Any]] = []
        self.packs: list[dict[str, Any]] = []
        self.superseded: list[dict[str, Any]] = []

    def route(self, call: Call) -> httpx.Response:
        if (call.method, call.path) == ("POST", "/packs"):
            return self._build(call.body)
        parts = call.path.strip("/").split("/")
        if call.method == "POST" and parts[0] == "packs" and parts[-1] == "supersede":
            self.superseded.append({"pack_id": parts[1], **call.body})
            for pack in self.packs:
                if pack["pack_id"] == parts[1]:
                    pack["superseded_by"] = "pack_next"
            return httpx.Response(200, json={"ok": True})
        return problem(404, f"no route {call.method} {call.path}")

    def _build(self, body: dict[str, Any]) -> httpx.Response:
        self.builds.append(body)
        snapshot = body["snapshot"]
        digest = hashlib.sha256(
            json.dumps(snapshot, sort_keys=True).encode()
        ).hexdigest()
        for pack in self.packs:
            if pack["content_hash"] == digest and pack["superseded_by"] is None:
                return httpx.Response(200, json=self._view(pack))
        # S5 refuses a snapshot that is not its strict ``PackContent``: so does
        # this fake, which is what makes the H1 snapshot builder answerable to
        # S5's real contract in every scene test.
        try:
            content = PackContent.model_validate(snapshot)
        except ValidationError as exc:
            reasons = [(e["loc"], e["msg"]) for e in exc.errors()]
            return problem(422, f"snapshot is not a PackContent: {reasons}")
        pack = {
            "pack_id": f"pack_{len(self.packs) + 1}",
            "case_id": body["case_id"],
            "version": len(self.packs) + 1,
            "content_hash": digest,
            "checks": content.checks.model_dump(),
            "content": content,
            "released_at": None,
            "superseded_by": None,
        }
        self.packs.append(pack)
        return httpx.Response(201, json=self._view(pack))

    @staticmethod
    def _view(pack: dict[str, Any]) -> dict[str, Any]:
        """The ``PackView`` S5 answers with: the frozen body plus its identity."""
        view = pack["content"].model_dump(
            mode="json", by_alias=True, exclude={"checks", "change_types"}
        )
        view.update(
            pack_id=pack["pack_id"],
            version=pack["version"],
            content_hash=pack["content_hash"],
            released_at=None,
            status="built",
            addenda=[],
        )
        return view


@pytest.fixture
def scoring_service() -> FakeScoringService:
    return FakeScoringService()


@pytest.fixture
def decision_service() -> FakeDecisionService:
    return FakeDecisionService()


@pytest.fixture
def scoring_client(scoring_service: FakeScoringService) -> ScoringClient:
    return ScoringClient(scoring_service.client("scoring"))


@pytest.fixture
def decision_client(decision_service: FakeDecisionService) -> DecisionClient:
    return DecisionClient(decision_service.client("decision"))


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


#: The domain each of the demo's four clauses is mapped to by the research agent.
DEMO_CLAUSE_DOMAINS = {
    "FFIEC-TPPP-04": "channel",
    "FCRM42-3.1": "geography",
    "TP71-2.3": "channel",
    "OFAC-1.2": "geography",
}


@pytest.fixture
def demo_research_output(demo: dict[str, Any]) -> list[dict[str, Any]]:
    """What the research agent keeps for FCRM-2026-0151: the four clauses, the
    five closest precedents and the one declared gap (the seed's).
    """
    clauses = [
        clause_proposal(c["clause_id"], DEMO_CLAUSE_DOMAINS[c["clause_id"]], c["quote"])
        for c in demo["clauses"]
    ]
    precedents = [precedent_match(case_id) for case_id in demo["precedents_closest"]]
    return [*clauses, *precedents, policy_gap("channel", demo["clause_gap"])]


@pytest.fixture
def demo_assessment_output(demo: dict[str, Any]) -> list[dict[str, Any]]:
    """What the assessment agent keeps for FCRM-2026-0151: likelihood and impact
    per domain and the thirteen controls at the seed's ratings (``score_before``,
    9.8 Band 2), plus the corridor review as a planned control (CTL-T01, no
    rating: never scored).
    """
    output: list[dict[str, Any]] = []
    for domain, area in demo["score_before"]["areas"].items():
        output.append(factor_proposal(domain, area["L"], area["I"]))
        output.extend(
            control_proposal(
                c["id"],
                domain,
                c["rating"],
                evidence=c["evidence"],
                evidence_date=c["evidence_date"],
            )
            for c in area["controls"]
        )
    output.append(
        control_proposal(
            "CTL-T01",
            "channel",
            None,
            planned=True,
            evidence="vendor_DD_scope_letter_2026.pdf",
            evidence_date="",
        )
    )
    return output


@pytest.fixture
def seed_results(
    agent_results: AgentResultStore, session: FakeSession
) -> Callable[..., Awaitable[str]]:
    """``await seed_results("research", *accepted)``: store one finished agent job
    as the engine would on ``agent.done`` (what a step reads back), returning its
    job id.
    """
    counter = {"n": 0}

    async def seed(
        agent: str,
        *accepted: dict[str, Any],
        job_id: Optional[str] = None,
        run_id: str = "run_1",
    ) -> str:
        counter["n"] += 1
        job = job_id or f"job_{agent}_{counter['n']}"
        await agent_results.save(
            agent_done_body(job, run_id, *accepted, agent=agent),
            session=session,  # type: ignore[arg-type]
        )
        return job

    return seed


def make_run(
    *,
    run_id: str = "run_1",
    case_id: str = "FCRM-2026-0151",
    state: CaseState = CaseState.INTAKE,
    refs: Optional[Mapping[str, Any]] = None,
    flags: Optional[Mapping[str, Any]] = None,
    band: Optional[int] = None,
) -> CaseRun:
    """A :class:`CaseRun` as a handler sees it (the snapshot taken when its
    step began), in ``state`` with ``refs``, run-snapshot ``flags`` and ``band``.
    """
    document = new_case_run_document(run_id=run_id, case_id=case_id, pins=DEFAULT_PINS)
    document["state"] = state.value
    document["refs"] = dict(refs or {})
    document["flags"] = {**document["flags"], **dict(flags or {})}
    document["band"] = band
    return case_run_from_document(document)


def harness_step(
    kind: StepKind,
    payload: Optional[Mapping[str, Any]] = None,
    *,
    step_id: str = "step_1",
    run_id: str = "run_1",
    case_id: str = "FCRM-2026-0151",
    trigger: str = "t",
) -> HarnessStep:
    """A ``harness.step`` as the engine enqueues it: ``payload`` is the
    triggering event's body.
    """
    return HarnessStep(
        step_id=step_id,
        step=kind,
        case_id=case_id,
        run_id=run_id,
        args={
            "trigger": trigger,
            "message_id": "evt_1",
            "payload": dict(payload or {}),
        },
    )


@pytest.fixture
def assessed_case(case_service: FakeCaseService) -> FakeCaseService:
    """The demo case as S1 holds it when assessment starts: three change types,
    three domains, submission v1.
    """
    case_service.change_types = ["vendor_onboarding", "new_geography", "new_product"]
    case_service.domains = ["channel", "geography", "product"]
    case_service.submission_version = 1
    return case_service


@pytest.fixture
async def published(
    assessed_case: FakeCaseService,
    step_deps_factory: Callable[..., StepDeps],
    seed_results: Callable[..., Awaitable[str]],
    demo_research_output: list[dict[str, Any]],
    demo_assessment_output: list[dict[str, Any]],
) -> tuple[StepResult, StepDeps]:
    """``assess.publish`` run once over the demo's research and assessment."""
    await seed_results("research", *demo_research_output, job_id="job_r1")
    await seed_results("assessment", *demo_assessment_output, job_id="job_a1")
    deps = step_deps_factory(make_run(run_id="run_1", state=CaseState.CRITIC))
    result = await assessment.assess_publish(
        harness_step(StepKind.ASSESS_PUBLISH, {"job_id": "job_a1"}, step_id="step_pub"),
        deps,
    )
    return result, deps


@pytest.fixture
def step_deps_factory(
    recording_audit: RecordingAudit,
    case_client: CaseClient,
    policy_client: PolicyClient,
    agent_results: AgentResultStore,
    scoring_client: ScoringClient,
    decision_client: DecisionClient,
    gate_store: GateStore,
) -> Callable[..., StepDeps]:
    """Builds the :class:`StepDeps` a handler gets, over the fakes."""

    def build(run: Optional[CaseRun] = None, **overrides: Any) -> StepDeps:
        values: dict[str, Any] = {
            "run": run if run is not None else make_run(),
            "audit": recording_audit,
            "case": case_client,
            "policy": policy_client,
            "agent_results": agent_results,
            "scoring": scoring_client,
            "decision": decision_client,
            "gates": gate_store,
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


class FakeDecisionVotesService(FakePeer):
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
def decision_votes_service(
    release_snapshot: Callable[..., dict[str, Any]],
) -> FakeDecisionVotesService:
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
    return FakeDecisionVotesService(body)


@pytest.fixture
def decision_votes_client(
    decision_votes_service: FakeDecisionVotesService,
) -> DecisionVotesClient:
    return DecisionVotesClient(decision_votes_service.client("decision"))


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


# --- the peers the oversight steps call (the examiner and the watchlist) -------


class FakeExaminerService(FakePeer):
    """S4's ``POST /examiner/answers`` as the real service behaves: an answer is
    stored once per question, an identical one again is a replay (200), a
    different one is a 409, and an answer citing a record beyond the chain head
    (``head``) is a 422.
    """

    BASE_PATH = "/api/fcrm/audit"

    def __init__(self) -> None:
        super().__init__()
        self.head = 99
        self.answers: dict[str, dict[str, Any]] = {}

    def route(self, call: Call) -> httpx.Response:
        if (call.method, call.path) != ("POST", "/examiner/answers"):
            return problem(404, f"no route {call.method} {call.path}")
        body = call.body or {}
        cited = {ref for s in body.get("sentences", []) for ref in s["refs"]}
        if any(ref > self.head for ref in cited):
            return problem(422, "cites a record the chain does not hold")
        stored = self.answers.get(body["question_id"])
        if stored is not None and stored != body:
            return coded_problem(409, "conflict")
        self.answers[body["question_id"]] = body
        out = {**body, "question": "the question as S4 holds it"}
        out.pop("run_id")
        return httpx.Response(200 if stored is not None else 201, json=out)


class FakeReflagService(FakePeer):
    """S1's internal re-flag routes: ``candidates`` is what the match returns for
    any query (filtered to the countries asked), ``rows`` the re-flags held, one
    per id. ``queries`` records the countries of each candidate request.
    """

    BASE_PATH = "/api/fcrm"

    def __init__(self) -> None:
        super().__init__()
        self.candidates: list[dict[str, Any]] = []
        self.rows: dict[str, dict[str, Any]] = {}
        self.queries: list[list[str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Keep every ``countries`` value (a plain ``dict`` would keep one)."""
        if request.url.path.endswith("/internal/reflags/candidates"):
            self.queries.append(request.url.params.get_list("countries"))
        return super().handler(request)

    def route(self, call: Call) -> httpx.Response:
        if (call.method, call.path) == ("GET", "/internal/reflags/candidates"):
            asked = set(self.queries[-1])
            items = [c for c in self.candidates if c["jurisdiction"] in asked]
            return httpx.Response(200, json={"items": items})
        if (call.method, call.path) == ("POST", "/internal/reflags"):
            body = call.body or {}
            created = 0
            for row in body["reflags"]:
                if row["reflag_id"] not in self.rows:
                    self.rows[row["reflag_id"]] = row
                    created += 1
            return httpx.Response(
                200,
                json={
                    "created": created,
                    "replayed": len(body["reflags"]) - created,
                    "reflag_ids": [r["reflag_id"] for r in body["reflags"]],
                },
            )
        return problem(404, f"no route {call.method} {call.path}")


class FakeJurisdictionPolicyService(FakePolicyService):
    """S3 with ``GET /jurisdictions/versions``: ``versions`` as
    ``(version, published)`` pairs.
    """

    def __init__(self) -> None:
        super().__init__()
        self.versions: list[tuple[str, str]] = [
            ("v2026-06", "2026-06-19"),
            ("v2026-10-fixture", "2026-10-01"),
        ]

    def route(self, call: Call) -> httpx.Response:
        if (call.method, call.path) == ("GET", "/jurisdictions/versions"):
            items = [
                {
                    "version": version,
                    "published": published,
                    "fixture": version.endswith("fixture"),
                    "active": version == self.versions[-1][0],
                }
                for version, published in self.versions
            ]
            return httpx.Response(
                200, json={"items": items, "next_cursor": None, "total": len(items)}
            )
        return super().route(call)


@pytest.fixture
def examiner_service() -> FakeExaminerService:
    return FakeExaminerService()


@pytest.fixture
def reflag_service() -> FakeReflagService:
    return FakeReflagService()


@pytest.fixture
def jurisdiction_policy_service() -> FakeJurisdictionPolicyService:
    return FakeJurisdictionPolicyService()
