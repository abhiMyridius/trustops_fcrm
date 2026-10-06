"""S4's assurance reads over the chain and the evidence ledger: how often analysts
replaced an agent's proposal, and which versions a case was pinned to.

Both are reads of what S4 already holds, through the insert-only role
(``find`` and ``aggregate``; nothing here writes). The agent quality of the eval
runs is another database and another credential: :mod:`fcrm_audit.quality`.

**Override rate.** ``human.override`` records are the numerator and the evidence
ledger's proposals the denominator, per agent, in **one aggregation**
(``$unionWith`` over ``proposals``), never a read per case. H1 writes a
``human.override`` only when an analyst replaced a control's rating or a
factor's likelihood and impact, which is the assessment agent's proposal, so a
record without an ``agent`` in its data is the assessment agent's.

**Pinned versions.** A case's pins are in the ``data.pins`` of its
``workflow.started`` record, written when its run opened. What a case opened now
would pin is read from the newest ``workflow.started`` that carries pins: every
new run pins the versions active when it opens (design A §3.1), so the newest
one is the best answer S4 can give without calling another service, and it is
as current as the last case opened. A seeded closed case has no pins in its
record and so no rows.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final, Optional, Protocol

from fcrm_common.errors import ValidationError
from fcrm_common.pagination import decode_cursor, encode_cursor
from fcrm_contracts.api.audit import AuditRecord, Proposal
from fcrm_contracts.api.audit_oversight import (
    OverrideRate,
    OverrideRowOut,
    OverridesPage,
    VersionsRowOut,
)
from pymongo.asynchronous.collection import AsyncCollection

logger = logging.getLogger(__name__)

TYPE_OVERRIDE: Final = "human.override"
TYPE_WORKFLOW: Final = "workflow.started"
#: The agent a record without an ``agent`` in its data belongs to: H1 records a
#: ``human.override`` only for a control rating or a factor's likelihood and
#: impact, which the assessment agent proposes.
DEFAULT_OVERRIDE_AGENT: Final = "assessment"
DEFAULT_PAGE_LIMIT: Final = 50
MAX_PAGE_LIMIT: Final = 200

#: Pinned version -> the name the Assurance screen shows, in display order.
PIN_LABELS: Final[Mapping[str, str]] = {
    "scoring_v": "Scoring model",
    "corpus_v": "Policy corpus",
    "controls_v": "Control inventory",
    "fieldset_v": "Field set",
    "jurisdictions_v": "Jurisdiction list",
    "routing_v": "Routing rules",
    "prompts_v": "Prompts",
}


def agent_of(data: Mapping[str, Any]) -> str:
    """The agent whose proposal a ``human.override`` replaced."""
    agent = data.get("agent")
    return agent if isinstance(agent, str) and agent else DEFAULT_OVERRIDE_AGENT


def _number(value: Any) -> float:
    """A decided value as one number: a control's ``rating``, a factor's
    likelihood times impact (its inherent value), else ``0``.
    """
    if not isinstance(value, Mapping):
        return 0.0
    rating = value.get("rating")
    if isinstance(rating, (int, float)) and not isinstance(rating, bool):
        return float(rating)
    likelihood, impact = value.get("likelihood"), value.get("impact")
    if isinstance(likelihood, (int, float)) and isinstance(impact, (int, float)):
        return float(likelihood * impact)
    return 0.0


#: The value H1 shows after an item in the summary: a control's rating
#: (``7.6``), a factor's ``L3 I4``, or ``-`` when it had none.
_TRAILING_VALUE: Final = re.compile(r"\s+(?:L\d+ I\d+|-?\d+(?:\.\d+)?|-)$")


def _item_of(summary: str) -> str:
    """What was overridden, from the record's summary
    (``CTL-T01 7.6 → 4.5 by u-riyer ...``): what precedes the first value.
    """
    head = summary.split(" → ", 1)[0]
    return _TRAILING_VALUE.sub("", head, count=1).strip() or head


def override_row(record: AuditRecord) -> Optional[OverrideRowOut]:
    """The row of one ``human.override`` record, or ``None`` for any other."""
    if record.type != TYPE_OVERRIDE:
        return None
    data = record.data
    reason = data.get("reason")
    return OverrideRowOut(
        case_id=record.case_id,
        item=_item_of(record.summary),
        proposed=_number(data.get("before")),
        final=_number(data.get("after")),
        by=record.actor,
        reason=reason if isinstance(reason, str) else "",
        agent=agent_of(data),
        date=record.created_at.isoformat(),
    )


def rate_rows(
    proposals: Mapping[str, int],
    overrides: Mapping[str, int],
    agent: Optional[str] = None,
) -> list[OverrideRate]:
    """Per-agent rates from the two counts, ordered by agent. An agent with no
    proposals has rate ``0`` (its overrides, if any, are still counted).
    """
    names = sorted({*proposals, *overrides})
    rows = [
        OverrideRate(
            agent=name,
            proposals=proposals.get(name, 0),
            overridden=overrides.get(name, 0),
            rate=(
                round(overrides.get(name, 0) / proposals[name], 4)
                if proposals.get(name)
                else 0.0
            ),
        )
        for name in names
    ]
    return [r for r in rows if agent is None or r.agent == agent]


def version_label(value: Any) -> str:
    """A pinned version as shown: ``v14`` for a number, the string as given."""
    return f"v{value}" if isinstance(value, int) else str(value)


def version_rows(
    pinned: Mapping[str, Any], current: Optional[Mapping[str, Any]]
) -> list[VersionsRowOut]:
    """The rows of a case's pins, in display order, against what is current."""
    rows: list[VersionsRowOut] = []
    for key, label in PIN_LABELS.items():
        if key not in pinned:
            continue
        version = version_label(pinned[key])
        now = version_label(current[key]) if current and key in current else None
        rows.append(
            VersionsRowOut(
                item=label,
                version=version,
                current=now,
                drifted=now is not None and now != version,
            )
        )
    return rows


def _pins_of(record: Optional[Mapping[str, Any]]) -> Optional[Mapping[str, Any]]:
    """The ``data.pins`` of a stored record document, if it has some."""
    if record is None:
        return None
    pins = (record.get("data") or {}).get("pins")
    return pins if isinstance(pins, Mapping) else None


def agent_filter(agent: str) -> dict[str, Any]:
    """The MongoDB filter for the overrides attributed to ``agent``."""
    if agent == DEFAULT_OVERRIDE_AGENT:
        return {
            "$or": [
                {"data.agent": agent},
                {"data.agent": {"$exists": False}},
                {"data.agent": None},
            ]
        }
    return {"data.agent": agent}


class AnalyticsStore(Protocol):
    """What the analytics routes depend on."""

    async def overrides(
        self, *, agent: Optional[str], cursor: Optional[str], limit: int
    ) -> OverridesPage:
        """The overrides (newest first) and the per-agent rates."""
        ...

    async def pinned(self, case_id: str) -> Optional[Mapping[str, Any]]:
        """The pins in ``case_id``'s ``workflow.started`` record, or ``None``."""
        ...

    async def current(self) -> Optional[Mapping[str, Any]]:
        """The pins of the newest ``workflow.started`` that carries some."""
        ...


def _bounds(limit: int) -> int:
    return max(1, min(limit, MAX_PAGE_LIMIT))


def _cursor_of(record: AuditRecord) -> str:
    return encode_cursor([record.created_at, record.case_id, record.seq])


def _after(cursor: str) -> tuple[datetime, str, int]:
    """The ``(created_at, case_id, seq)`` a page continues after.

    Raises:
        fcrm_common.errors.ValidationError: ``cursor`` is not one S4 produced.
    """
    decoded = decode_cursor(cursor)
    if (
        not isinstance(decoded, list)
        or len(decoded) != 3
        or not isinstance(decoded[0], datetime)
        or not isinstance(decoded[1], str)
        or not isinstance(decoded[2], int)
    ):
        raise ValidationError(
            "Invalid pagination cursor.",
            errors=[{"loc": "query.cursor", "msg": "malformed cursor"}],
        )
    return decoded[0], decoded[1], decoded[2]


class InMemoryAnalyticsStore:
    """The unit tests' store, over the chains and the ledger handed in.

    Args:
        records: Every audit record of every case.
        proposals: Every ledger entry.
    """

    def __init__(
        self,
        records: Sequence[AuditRecord] = (),
        proposals: Sequence[Proposal] = (),
    ) -> None:
        self.records = list(records)
        self.proposals = list(proposals)

    async def overrides(
        self, *, agent: Optional[str], cursor: Optional[str], limit: int
    ) -> OverridesPage:
        size = _bounds(limit)
        found = [
            r
            for r in self.records
            if r.type == TYPE_OVERRIDE and (agent is None or agent_of(r.data) == agent)
        ]
        found.sort(key=lambda r: (-r.created_at.timestamp(), r.case_id, r.seq))
        if cursor is not None:
            at, case_id, seq = _after(cursor)
            marker = (-at.timestamp(), case_id, seq)
            found = [
                r
                for r in found
                if (-r.created_at.timestamp(), r.case_id, r.seq) > marker
            ]
        window = found[: size + 1]
        page = window[:size]
        proposal_counts: dict[str, int] = {}
        for entry in self.proposals:
            proposal_counts[entry.agent] = proposal_counts.get(entry.agent, 0) + 1
        override_counts: dict[str, int] = {}
        for record in self.records:
            if record.type == TYPE_OVERRIDE:
                name = agent_of(record.data)
                override_counts[name] = override_counts.get(name, 0) + 1
        rows = [row for r in page if (row := override_row(r)) is not None]
        return OverridesPage(
            items=rows,
            next_cursor=_cursor_of(page[-1]) if len(window) > size and page else None,
            total=len(
                [
                    r
                    for r in self.records
                    if r.type == TYPE_OVERRIDE
                    and (agent is None or agent_of(r.data) == agent)
                ]
            ),
            rates=rate_rows(proposal_counts, override_counts, agent),
        )

    async def pinned(self, case_id: str) -> Optional[Mapping[str, Any]]:
        for record in sorted(self.records, key=lambda r: r.seq):
            if record.case_id == case_id and record.type == TYPE_WORKFLOW:
                pins = record.data.get("pins")
                return pins if isinstance(pins, Mapping) else None
        return None

    async def current(self) -> Optional[Mapping[str, Any]]:
        newest = [
            r
            for r in self.records
            if r.type == TYPE_WORKFLOW and isinstance(r.data.get("pins"), Mapping)
        ]
        if not newest:
            return None
        latest = max(newest, key=lambda r: (r.created_at, r.case_id))
        pins: Mapping[str, Any] = latest.data["pins"]
        return pins


class MongoAnalyticsStore:
    """The real store over ``audit_records`` and ``proposals`` (``find`` and
    ``aggregate`` only: the audit role grants no write).

    Args:
        records: The ``audit_records`` collection.
        proposals: The ``proposals`` collection (the ``$unionWith`` source).
    """

    def __init__(
        self, records: AsyncCollection[Any], proposals: AsyncCollection[Any]
    ) -> None:
        self._records = records
        self._proposals = proposals

    async def overrides(
        self, *, agent: Optional[str], cursor: Optional[str], limit: int
    ) -> OverridesPage:
        size = _bounds(limit)
        match: dict[str, Any] = {"type": TYPE_OVERRIDE}
        if agent is not None:
            match = {"$and": [match, agent_filter(agent)]}
        query: dict[str, Any] = match
        if cursor is not None:
            at, case_id, seq = _after(cursor)
            query = {
                "$and": [
                    match,
                    {
                        "$or": [
                            {"created_at": {"$lt": at}},
                            {"created_at": at, "case_id": {"$gt": case_id}},
                            {"created_at": at, "case_id": case_id, "seq": {"$gt": seq}},
                        ]
                    },
                ]
            }
        docs = [
            doc
            async for doc in self._records.find(
                query,
                projection={"_id": 0},
                sort=[("created_at", -1), ("case_id", 1), ("seq", 1)],
                limit=size + 1,
            )
        ]
        records = [AuditRecord.model_validate(doc) for doc in docs]
        page = records[:size]
        return OverridesPage(
            items=[row for r in page if (row := override_row(r)) is not None],
            next_cursor=_cursor_of(page[-1]) if len(records) > size and page else None,
            total=await self._records.count_documents(match),
            rates=await self._rates(agent),
        )

    async def _rates(self, agent: Optional[str]) -> list[OverrideRate]:
        """One aggregation: overrides per agent from the chain, unioned with the
        ledger's proposals per agent, summed.
        """
        pipeline: list[dict[str, Any]] = [
            {"$match": {"type": TYPE_OVERRIDE}},
            {
                "$group": {
                    "_id": {"$ifNull": ["$data.agent", DEFAULT_OVERRIDE_AGENT]},
                    "overridden": {"$sum": 1},
                    "proposals": {"$sum": 0},
                }
            },
            {
                "$unionWith": {
                    "coll": self._proposals.name,
                    "pipeline": [
                        {
                            "$group": {
                                "_id": "$agent",
                                "overridden": {"$sum": 0},
                                "proposals": {"$sum": 1},
                            }
                        }
                    ],
                }
            },
            {
                "$group": {
                    "_id": "$_id",
                    "overridden": {"$sum": "$overridden"},
                    "proposals": {"$sum": "$proposals"},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        cursor = await self._records.aggregate(pipeline)
        rows = [doc async for doc in cursor]
        return rate_rows(
            {str(r["_id"]): int(r["proposals"]) for r in rows},
            {str(r["_id"]): int(r["overridden"]) for r in rows},
            agent,
        )

    async def pinned(self, case_id: str) -> Optional[Mapping[str, Any]]:
        doc = await self._records.find_one(
            {"case_id": case_id, "type": TYPE_WORKFLOW},
            projection={"data": 1},
            sort=[("seq", 1)],
        )
        return _pins_of(doc)

    async def current(self) -> Optional[Mapping[str, Any]]:
        doc = await self._records.find_one(
            {"type": TYPE_WORKFLOW, "data.pins": {"$exists": True}},
            projection={"data": 1},
            sort=[("created_at", -1)],
        )
        return _pins_of(doc)
