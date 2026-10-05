"""The ``gates`` collection (Guard 7; design A §3.2) and the thin wrapper
around :func:`fcrm_guardrails.human_gate.validate_gate_decision` that
turns a Mongo document into the :class:`~fcrm_guardrails.human_gate
.GateRecord` that function already validates against.

Schema, verbatim from design A §3.2: ``_id (gate_)``, ``case_id, run_id,
gate_type, subject{kind, id}, required_role, opened_at, deadline?, status
(open|decided|expired), decision, reason, decided_by, decided_at, before,
after``. A gate is opened by Caseflow (this module, called from
:mod:`fcrm_caseflow.engine`'s dispatch step) and decided through the
owning service's route, which publishes ``gate.decided``
(:class:`fcrm_contracts.common.GateDecision`) back onto ``case.events``;
Caseflow matches ``gate_id``, refuses the wrong role/reason (Guard 7),
and advances.
"""

from __future__ import annotations

from typing import Any, Final, Optional

from fcrm_common.mongo import Document
from fcrm_common.time import utcnow
from fcrm_contracts.common import GateDecision, Principal
from fcrm_contracts.enums import GateStatus, GateType, Role
from fcrm_guardrails.base import AUDIT_TYPES, GuardName, GuardResult, GuardStatus
from fcrm_guardrails.human_gate import GateRecord, validate_gate_decision
from pymongo import ASCENDING, IndexModel
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

GATES_COLLECTION: Final = "gates"


class GateDecisionError(Exception):
    """A ``gate.decided`` event failed Guard 7
    (:func:`~fcrm_guardrails.human_gate.validate_gate_decision`) — wrong
    role, wrong gate, stale status or a missing required reason. Raised by
    :mod:`fcrm_caseflow.engine`'s handler so the base ``Consumer`` treats
    it as a poison/retry-worthy failure rather than silently advancing the
    state machine on an invalid decision.
    """

    def __init__(self, result: GuardResult) -> None:
        self.result = result
        super().__init__(result.summary)


def gate_document(
    *,
    gate_id: str,
    case_id: str,
    run_id: str,
    gate_type: GateType,
    required_role: Role,
    subject: Optional[dict[str, str]] = None,
) -> Document:
    """A freshly opened gate (``status: open``)."""
    return {
        "_id": gate_id,
        "case_id": case_id,
        "run_id": run_id,
        "gate_type": gate_type.value,
        "subject": dict(subject or {}),
        "required_role": required_role.value,
        "opened_at": utcnow(),
        "deadline": None,
        "status": GateStatus.OPEN.value,
        "decision": None,
        "reason": None,
        "decided_by": None,
        "decided_at": None,
        "before": None,
        "after": None,
    }


def _record_to_document(doc: Document) -> GateRecord:
    return GateRecord(
        gate_id=doc["_id"],
        gate_type=GateType(doc["gate_type"]),
        case_id=doc["case_id"],
        run_id=doc["run_id"],
        required_role=Role(doc["required_role"]),
        status=GateStatus(doc["status"]),
        subject=dict(doc.get("subject", {})),
    )


class GateStore:
    """Typed access to the ``gates`` collection, scoped to one database.

    Every write takes the caller's transaction ``session`` (same rule as
    :class:`fcrm_caseflow.store.CaseRunStore`): gate state changes
    together with the case-run transition and the outbox row they imply,
    never separately.
    """

    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def ensure_indexes(self) -> list[str]:
        """Fast "how many open gates of this type" counts
        (:meth:`count_open`), the aggregation
        ``gate.decided.po_confirm_category.all``/``internal
        .all_items_decided`` key on.
        """
        return await self._collection.create_indexes(
            [
                IndexModel(
                    [
                        ("case_id", ASCENDING),
                        ("gate_type", ASCENDING),
                        ("status", ASCENDING),
                    ],
                    name="case_type_status",
                )
            ]
        )

    async def open(
        self, document: Document, *, session: Optional[AsyncClientSession] = None
    ) -> None:
        """Insert a freshly opened gate."""
        await self._collection.insert_one(document, session=session)

    async def open_if_absent(
        self, document: Document, *, session: AsyncClientSession
    ) -> bool:
        """Insert ``document`` unless a gate with the same ``_id`` already
        exists; returns whether *this call* created it.

        For gates with a deterministic id (:func:`fcrm_common.ids
        .deterministic_id`), where a retry derives the id it used before. It
        is an upsert with ``$setOnInsert`` rather than ``insert_one`` plus a
        caught ``DuplicateKeyError`` because inside a MongoDB transaction a
        duplicate-key error aborts the whole transaction: catching it would
        leave the caller committing a dead transaction. An existing gate (open
        or decided) is left exactly as it is.
        """
        body = {k: v for k, v in document.items() if k != "_id"}
        result = await self._collection.update_one(
            {"_id": document["_id"]},
            {"$setOnInsert": body},
            upsert=True,
            session=session,
        )
        return result.upserted_id is not None

    async def get(
        self, gate_id: str, *, session: Optional[AsyncClientSession] = None
    ) -> Optional[Document]:
        return await self._collection.find_one({"_id": gate_id}, session=session)

    async def count_open(
        self,
        case_id: str,
        gate_type: GateType,
        *,
        run_id: Optional[str] = None,
        session: Optional[AsyncClientSession] = None,
    ) -> int:
        """Open gates of ``gate_type`` for ``case_id`` (optionally scoped to
        one ``run_id``) — the "all decided" aggregation this package's
        wire-event resolution needs (:mod:`fcrm_caseflow.resolve`).
        """
        query: dict[str, Any] = {
            "case_id": case_id,
            "gate_type": gate_type.value,
            "status": GateStatus.OPEN.value,
        }
        if run_id is not None:
            query["run_id"] = run_id
        return await self._collection.count_documents(query, session=session)

    async def decide(
        self,
        decision: GateDecision,
        principal: Principal,
        *,
        session: Optional[AsyncClientSession] = None,
    ) -> Document:
        """Validate ``decision`` against the stored gate (Guard 7) and, if
        valid, mark it ``decided``. Returns the gate document *before* this
        update (so a caller can still read its pre-decision ``subject``).

        Raises:
            GateDecisionError: the gate does not exist, or Guard 7 refused
                the decision (wrong gate/role/reason — see
                :func:`~fcrm_guardrails.human_gate.validate_gate_decision`).
        """
        doc = await self.get(decision.gate_id, session=session)
        if doc is None:
            result = GuardResult(
                guard=GuardName.HUMAN_GATE,
                status=GuardStatus.BLOCKED,
                summary=f"no gate {decision.gate_id!r} is open for {decision.case_id}",
                details={"gate_id": decision.gate_id},
                audit_type=AUDIT_TYPES[GuardName.HUMAN_GATE],
            )
            raise GateDecisionError(result)
        record = _record_to_document(doc)
        result = validate_gate_decision(record, decision, principal)
        if not result.passed:
            raise GateDecisionError(result)
        await self._collection.update_one(
            {"_id": decision.gate_id},
            {
                "$set": {
                    "status": GateStatus.DECIDED.value,
                    "decision": decision.decision,
                    "reason": decision.reason,
                    "decided_by": decision.decided_by,
                    "decided_at": decision.decided_at,
                    "before": decision.before,
                    "after": decision.after,
                }
            },
            session=session,
        )
        return doc
