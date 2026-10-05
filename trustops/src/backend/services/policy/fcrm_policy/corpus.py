"""S3's policy corpus as the system of record holds it: one clause per
document in ``clauses`` (design A §2.3, §6.1; ADR-29).

Three things read it, and none of them can drift from the others because they
all read this collection:

* ``GET /clauses/{id}`` serves one clause (verbatim text, both citations,
  provenance);
* ``GET /internal/clauses`` pages the whole corpus for H4, which embeds it;
* search shapes its hits from the same documents (:mod:`fcrm_policy.search`).

:class:`ClauseStore` is what the routes depend on: :class:`MongoClauseStore`
in production, :class:`InMemoryClauseStore` (built from the same
:func:`~fcrm_policy.seedmodel.clause_documents`) in the unit tests. The text of
a clause is never generated or rewritten here: :func:`clause_out` copies the
stored ``text`` as it is.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any, Final, Optional, Protocol

from fcrm_common.pagination import decode_cursor, encode_cursor
from fcrm_contracts.api.policy import ClauseDetailOut
from fcrm_contracts.common import Page
from fcrm_contracts.proposals.provenance import ClauseSourceRef
from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_policy.store import Document

logger = logging.getLogger(__name__)

MAX_CLAUSE_PAGE: Final = 200
"""Largest page ``GET /internal/clauses`` serves (the corpus is 197 clauses)."""
DEFAULT_CLAUSE_PAGE: Final = 100


def corpus_label(version: int) -> str:
    """``14`` -> ``"v14"``: the label the web and the seed use."""
    return f"v{version}"


def clause_out(doc: Mapping[str, Any]) -> ClauseDetailOut:
    """One stored clause as served: its text verbatim, with the structured
    citation an agent claim carries (``source_ref``) next to the readable one
    (``ref``).
    """
    version = int(doc["corpus_version"])
    return ClauseDetailOut(
        clause_id=doc["clause_id"],
        source_id=doc["source_id"],
        title=doc["title"],
        text=doc["text"],
        ref=doc["ref"],
        source_ref=ClauseSourceRef(clause_id=doc["clause_id"], corpus_v=version),
        corpus_version=version,
        page=doc.get("page"),
        line_start=doc.get("line_start"),
        line_end=doc.get("line_end"),
        risk_areas=list(doc.get("risk_areas", [])),
        trigger_types=list(doc.get("trigger_types", [])),
        jurisdictions=list(doc.get("jurisdictions", [])),
        quotable=bool(doc.get("quotable", True)),
        provenance_kind=doc.get("provenance_kind"),
    )


class ClauseStore(Protocol):
    """What the corpus routes depend on."""

    async def latest_version(self) -> Optional[int]:
        """The newest corpus version held, or ``None`` for an empty corpus."""
        ...

    async def get_clause(
        self, clause_id: str, corpus_version: Optional[int]
    ) -> Optional[ClauseDetailOut]:
        """One clause at ``corpus_version`` (the latest when ``None``)."""
        ...

    async def page(
        self, corpus_version: int, *, cursor: Optional[str], limit: int
    ) -> Page[ClauseDetailOut]:
        """One page of a corpus version, in id order."""
        ...


class InMemoryClauseStore:
    """The unit-test stand-in, over the same documents the Mongo store holds."""

    def __init__(self, docs: Sequence[Mapping[str, Any]]) -> None:
        """Hold ``docs`` (``fcrm_policy.seedmodel.clause_documents()``)."""
        self._docs = sorted(
            (dict(d) for d in docs), key=lambda d: (d["corpus_version"], d["_id"])
        )

    async def latest_version(self) -> Optional[int]:
        """The highest ``corpus_version`` held."""
        return max((int(d["corpus_version"]) for d in self._docs), default=None)

    async def get_clause(
        self, clause_id: str, corpus_version: Optional[int]
    ) -> Optional[ClauseDetailOut]:
        """The clause at ``corpus_version``, the latest version when ``None``."""
        version = corpus_version
        if version is None:
            version = await self.latest_version()
        found = next(
            (
                d
                for d in self._docs
                if d["clause_id"] == clause_id and d["corpus_version"] == version
            ),
            None,
        )
        return clause_out(found) if found is not None else None

    async def page(
        self, corpus_version: int, *, cursor: Optional[str], limit: int
    ) -> Page[ClauseDetailOut]:
        """Seek pagination by ``_id``, like the Mongo store."""
        after = decode_cursor(cursor) if cursor is not None else None
        rows = [
            d
            for d in self._docs
            if d["corpus_version"] == corpus_version
            and (after is None or d["_id"] > after)
        ]
        window = rows[:limit]
        more = len(rows) > limit
        return Page(
            items=[clause_out(d) for d in window],
            next_cursor=encode_cursor(window[-1]["_id"]) if more else None,
            total=sum(1 for d in self._docs if d["corpus_version"] == corpus_version),
        )


class MongoClauseStore:
    """The real store over the ``clauses`` collection."""

    def __init__(self, collection: AsyncCollection[Document]) -> None:
        """Read ``collection`` (the same one the keyword search ranks)."""
        self._collection = collection

    async def ensure_indexes(self) -> None:
        """The two read paths: a clause by id within a version, and a version
        in ``_id`` order (the paging seek). Idempotent.
        """
        await self._collection.create_indexes(
            [
                IndexModel(
                    [("clause_id", ASCENDING), ("corpus_version", ASCENDING)],
                    name="clause_by_version",
                ),
                IndexModel(
                    [("corpus_version", ASCENDING), ("_id", ASCENDING)],
                    name="version_id_order",
                ),
            ]
        )

    async def latest_version(self) -> Optional[int]:
        """The highest ``corpus_version`` (one indexed read)."""
        top = await self._collection.find_one(
            {}, projection={"corpus_version": 1}, sort=[("corpus_version", DESCENDING)]
        )
        return int(top["corpus_version"]) if top is not None else None

    async def get_clause(
        self, clause_id: str, corpus_version: Optional[int]
    ) -> Optional[ClauseDetailOut]:
        """The clause at ``corpus_version``, the latest version when ``None``."""
        version = corpus_version
        if version is None:
            version = await self.latest_version()
        if version is None:
            return None
        doc = await self._collection.find_one(
            {"clause_id": clause_id, "corpus_version": version}
        )
        return clause_out(doc) if doc is not None else None

    async def page(
        self, corpus_version: int, *, cursor: Optional[str], limit: int
    ) -> Page[ClauseDetailOut]:
        """Seek pagination by ``_id`` within one corpus version."""
        base: dict[str, Any] = {"corpus_version": corpus_version}
        query = base
        if cursor is not None:
            query = {"$and": [base, {"_id": {"$gt": decode_cursor(cursor)}}]}
        docs = await self._collection.find(
            query, sort=[("_id", ASCENDING)], limit=limit + 1
        ).to_list()
        window = docs[:limit]
        next_cursor = encode_cursor(window[-1]["_id"]) if len(docs) > limit else None
        return Page(
            items=[clause_out(d) for d in window],
            next_cursor=next_cursor,
            total=await self._collection.count_documents(base),
        )


__all__ = [
    "DEFAULT_CLAUSE_PAGE",
    "MAX_CLAUSE_PAGE",
    "ClauseStore",
    "InMemoryClauseStore",
    "MongoClauseStore",
    "clause_out",
    "corpus_label",
]
