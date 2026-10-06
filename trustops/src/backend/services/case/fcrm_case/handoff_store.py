"""S1's record of the ``handoff`` gates Caseflow opened (flow F-path).

Same split as :mod:`fcrm_case.reflag_store`: a small :class:`HandoffGateStore`
``Protocol``, a Mongo implementation the lifespan wires and an in-memory twin for
unit tests. One collection in ``fcrm_case``:

================  ===========================================================
``handoff_gates`` ``_id = gate_id`` (the id Caseflow derived from the run and the
                  failed job): the failed job, why it failed and the stage the
                  run was in. ``{case_id}`` serves a case's own list.
================  ===========================================================

**Idempotent.** The row is written from ``gate.opened`` (the consumer in
:mod:`fcrm_case.consumer`), in the consumer's transaction, with ``$setOnInsert``:
a redelivered event, or the same failure reported twice, creates nothing new.

Whether a gate is still open is not stored here: it is open until the analyst's
ledger (``analyst_decisions``) holds a decision on it, the same rule the
completeness gate follows.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final, Optional, Protocol

from fcrm_common.mongo import Document, from_bson, to_bson
from pymongo import ASCENDING, IndexModel
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_case.domain.analyst_handoff import HandoffGateRow

#: A case has a handful of handoff gates; this only stops a bug reading a million.
MAX_GATES_PER_CASE: Final = 200


class HandoffGateStore(Protocol):
    """What the consumer and the handoff routes need."""

    async def ensure_indexes(self) -> None:
        """Create the index the query below relies on (idempotent)."""
        ...

    async def record(
        self, row: HandoffGateRow, *, session: Optional[AsyncClientSession] = None
    ) -> bool:
        """Hold ``row`` unless its gate is already held; ``True`` when this call
        created it.
        """
        ...

    async def get(self, gate_id: str) -> Optional[HandoffGateRow]:
        """One gate, or ``None``."""
        ...

    async def for_case(self, case_id: str) -> list[HandoffGateRow]:
        """The case's gates, oldest first."""
        ...


def _row_of(doc: Mapping[str, Any]) -> HandoffGateRow:
    """A stored document as a row: ``_id`` is the gate id and is not repeated as a
    field."""
    data = from_bson(dict(doc))
    data["gate_id"] = data.pop("_id")
    return HandoffGateRow.model_validate(data)


class MongoHandoffGateStore:
    """The real store over ``fcrm_case.handoff_gates``.

    Args:
        db: The ``fcrm_case`` database.
    """

    def __init__(self, db: Any) -> None:
        self._rows: AsyncCollection[Document] = db["handoff_gates"]

    async def ensure_indexes(self) -> None:
        """``handoff_gates{case_id, opened_at}``; the rest is read by ``_id``."""
        await self._rows.create_indexes(
            [
                IndexModel(
                    [("case_id", ASCENDING), ("opened_at", ASCENDING)],
                    name="case_opened_idx",
                )
            ]
        )

    async def record(
        self, row: HandoffGateRow, *, session: Optional[AsyncClientSession] = None
    ) -> bool:
        fields = to_bson(row.model_dump(mode="python", exclude={"gate_id"}))
        result = await self._rows.update_one(
            {"_id": row.gate_id},
            {"$setOnInsert": fields},
            upsert=True,
            session=session,
        )
        return result.upserted_id is not None

    async def get(self, gate_id: str) -> Optional[HandoffGateRow]:
        doc = await self._rows.find_one({"_id": gate_id})
        return _row_of(doc) if doc is not None else None

    async def for_case(self, case_id: str) -> list[HandoffGateRow]:
        cursor = self._rows.find(
            {"case_id": case_id},
            sort=[("opened_at", ASCENDING), ("_id", ASCENDING)],
            limit=MAX_GATES_PER_CASE,
        )
        return [_row_of(doc) async for doc in cursor]


class InMemoryHandoffGateStore:
    """The test twin."""

    def __init__(self) -> None:
        self.rows: dict[str, HandoffGateRow] = {}

    async def ensure_indexes(self) -> None:
        """Nothing to create."""

    async def record(
        self, row: HandoffGateRow, *, session: Optional[AsyncClientSession] = None
    ) -> bool:
        if row.gate_id in self.rows:
            return False
        self.rows[row.gate_id] = row
        return True

    async def get(self, gate_id: str) -> Optional[HandoffGateRow]:
        return self.rows.get(gate_id)

    async def for_case(self, case_id: str) -> list[HandoffGateRow]:
        rows = [r for r in self.rows.values() if r.case_id == case_id]
        return sorted(rows, key=lambda r: (r.opened_at, r.gate_id))
