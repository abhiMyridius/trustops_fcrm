"""S3's keyword-search side: one ``KeywordCollection`` per index
(``policy_clauses``, ``precedent_cases``, ``controls``), each backed by a
Mongo ``$text`` query in production and, for the no-live-Mongo unit test
tier, an in-memory TF cosine stand-in -- the same two-tier shape
``fcrm_scoring.store.ConfigStore`` already uses (``MongoConfigStore`` /
``InMemoryConfigStore``), applied here to keyword ranking instead of
config lookup.

This module owns S3's *own* document schema (``clauses``/``precedents``/
``controls``, each carrying a precomputed ``search_text`` field -- see
``fcrm_policy.seedmodel``) and the filter translation between a request's
``SearchFilters`` and that schema's field names. It is deliberately
separate from :mod:`fcrm_policy.search.filters`: that module validates the
Index Contract's *vector* metadata keys (a different, narrower model H4's
vector store uses, with list fields joined into one string); this one
filters S3's own richer Mongo documents, which keep list fields as real
arrays. Reusing the Index Contract's ``matches_filters`` here would
silently misapply its "joined string" semantics to arrays it was never
built for -- so this module has its own small, explicit field map instead
of reimplementing (or misusing) that one.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from pymongo import IndexModel
from pymongo.asynchronous.collection import AsyncCollection

logger = logging.getLogger(__name__)

POLICY_CLAUSES = "policy_clauses"
PRECEDENT_CASES = "precedent_cases"
CONTROLS = "controls"
KNOWN_INDEXES: frozenset[str] = frozenset({POLICY_CLAUSES, PRECEDENT_CASES, CONTROLS})

Document = dict[str, Any]

#: Which S3 document field a ``SearchFilters`` key maps to, per index. A
#: filter key absent from an index's map is simply not applicable there
#: (e.g. ``closed_date_before`` only means something for precedents).
_FIELD_MAP: dict[str, dict[str, str]] = {
    POLICY_CLAUSES: {
        "corpus_version": "corpus_version",
        "trigger_type": "trigger_types",
        "risk_area": "risk_areas",
        "jurisdiction": "jurisdictions",
    },
    PRECEDENT_CASES: {
        "trigger_type": "change_types",
        "risk_area": "domains",
        "jurisdiction": "geographies",
        "closed_date_before": "closed_date",
    },
    CONTROLS: {
        "risk_area": "domain",
    },
}
#: Fields holding a real Mongo array (membership match), vs. a scalar
#: (equality) or the one range comparison (``closed_date_before``).
_ARRAY_FIELDS: frozenset[str] = frozenset(
    {
        "trigger_types",
        "risk_areas",
        "jurisdictions",
        "change_types",
        "domains",
        "geographies",
    }
)


def mongo_filter(index: str, filters: Mapping[str, Any]) -> dict[str, Any]:
    """Translate request ``filters`` into a Mongo query fragment for
    ``index``, pushing every constraint down to the server rather than
    filtering client-side after the fact.
    """
    field_map = _FIELD_MAP.get(index, {})
    query: dict[str, Any] = {}
    for key, value in filters.items():
        if value is None:
            continue
        field = field_map.get(key)
        if field is None:
            continue
        if key == "closed_date_before":
            query[field] = {"$lt": value}
        else:
            # Equality on an array field is Mongo's membership match; on a
            # scalar field it is just equality -- the same operator works
            # for both, so no branch is needed here.
            query[field] = value
    return query


def _matches(index: str, doc: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    """The in-memory twin of :func:`mongo_filter`: the same field map,
    applied as a Python predicate instead of a query fragment.
    """
    field_map = _FIELD_MAP.get(index, {})
    for key, value in filters.items():
        if value is None:
            continue
        field = field_map.get(key)
        if field is None:
            continue
        if key == "closed_date_before":
            if not (str(doc.get(field, "")) < value):
                return False
        elif field in _ARRAY_FIELDS:
            if value not in doc.get(field, []):
                return False
        elif doc.get(field) != value:
            return False
    return True


class KeywordCollection(Protocol):
    """One index's keyword-ranking source: rank ids by ``query``, then
    fetch the authoritative documents for a fused id list.
    """

    async def text_search(
        self, query: str, *, filters: Mapping[str, Any], limit: int
    ) -> list[tuple[str, float]]: ...

    async def get_by_ids(self, ids: Sequence[str]) -> dict[str, Document]: ...


class MongoKeywordCollection:
    """A real Mongo ``$text`` query over one of S3's own collections.

    Every document carries a ``search_text`` field (``fcrm_policy.
    seedmodel``), so one text index (``search_text``) serves all three
    collections identically -- no per-index field list here. Ranking and
    lookup key on ``result_id``, not Mongo's own ``_id`` (clauses' ``_id``
    is a versioned compound key; see ``fcrm_policy.seedmodel``'s module
    docstring).
    """

    def __init__(self, collection: AsyncCollection[Document], index: str) -> None:
        self._collection = collection
        self._index = index

    @property
    def collection(self) -> AsyncCollection[Document]:
        """The underlying collection, for seed loading and index setup."""
        return self._collection

    async def ensure_text_index(self) -> None:
        """Create the ``search_text`` text index and a ``result_id``
        lookup index if missing (idempotent: ``create_indexes`` on an
        index that already exists is a no-op).
        """
        await self._collection.create_indexes(
            [
                IndexModel([("search_text", "text")], name="search_text_text"),
                IndexModel([("result_id", 1)], name="result_id_lookup"),
            ]
        )

    async def text_search(
        self, query: str, *, filters: Mapping[str, Any], limit: int
    ) -> list[tuple[str, float]]:
        """Ranked ``(result_id, text_score)`` pairs, best match first."""
        mongo_query: dict[str, Any] = {
            "$text": {"$search": query},
            **mongo_filter(self._index, filters),
        }
        cursor = self._collection.find(
            mongo_query, {"result_id": 1, "_score": {"$meta": "textScore"}}
        ).sort([("_score", {"$meta": "textScore"})])
        docs = await cursor.to_list(length=limit)
        return [(str(doc["result_id"]), float(doc["_score"])) for doc in docs]

    async def get_by_ids(self, ids: Sequence[str]) -> dict[str, Document]:
        if not ids:
            return {}
        docs = await self._collection.find({"result_id": {"$in": list(ids)}}).to_list(
            length=len(ids)
        )
        return {str(doc["result_id"]): doc for doc in docs}


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> Counter[str]:
    return Counter(_TOKEN_RE.findall(text.lower()))


def _cosine(query: Counter[str], doc: Counter[str]) -> float:
    dot = sum(count * doc.get(token, 0) for token, count in query.items())
    if dot == 0:
        return 0.0
    q_norm = math.sqrt(sum(v * v for v in query.values()))
    d_norm = math.sqrt(sum(v * v for v in doc.values()))
    return dot / (q_norm * d_norm)


class InMemoryKeywordCollection:
    """A TF-cosine stand-in for :class:`MongoKeywordCollection`, used by
    unit tests with no live Mongo (the same role ``InMemoryConfigStore``
    plays for S2). Deliberately simpler than ``fcrm_tools.retrieval.
    KeywordIndex`` (no IDF weighting) -- good enough to rank S3's own tiny
    seeded collections (4 clauses, 65 precedents, 24 controls), and kept
    local rather than depending on ``fcrm-tools`` for one small pure
    function.
    """

    def __init__(self, docs: Sequence[Mapping[str, Any]], index: str) -> None:
        self._index = index
        self._docs: dict[str, Document] = {str(d["result_id"]): dict(d) for d in docs}
        self._tokens: dict[str, Counter[str]] = {
            doc_id: _tokens(str(doc.get("search_text", "")))
            for doc_id, doc in self._docs.items()
        }

    async def text_search(
        self, query: str, *, filters: Mapping[str, Any], limit: int
    ) -> list[tuple[str, float]]:
        query_tokens = _tokens(query)
        scored = [
            (doc_id, _cosine(query_tokens, self._tokens[doc_id]))
            for doc_id, doc in self._docs.items()
            if _matches(self._index, doc, filters)
        ]
        ranked = sorted(
            (item for item in scored if item[1] > 0.0),
            key=lambda item: (-item[1], item[0]),
        )
        return ranked[:limit]

    async def get_by_ids(self, ids: Sequence[str]) -> dict[str, Document]:
        return {doc_id: self._docs[doc_id] for doc_id in ids if doc_id in self._docs}


def build_in_memory_collections(
    raw: Mapping[str, Any],
) -> dict[str, InMemoryKeywordCollection]:
    """The three in-memory collections, seeded from ``raw`` (``fcrm_seed.
    loader.load_raw()`` or an equivalent fixture).
    """
    from fcrm_policy.seedmodel import (
        clause_documents,
        control_documents,
        precedent_documents,
    )

    return {
        POLICY_CLAUSES: InMemoryKeywordCollection(clause_documents(), POLICY_CLAUSES),
        PRECEDENT_CASES: InMemoryKeywordCollection(
            precedent_documents(raw), PRECEDENT_CASES
        ),
        CONTROLS: InMemoryKeywordCollection(control_documents(raw), CONTROLS),
    }


def clause_metadata(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The ``SearchHit.metadata`` shape for a ``policy_clauses`` hit."""
    return {
        "source_id": doc.get("source_id"),
        "corpus_version": doc.get("corpus_version"),
        "ref": doc.get("ref"),
        "title": doc.get("title"),
        "quotable": bool(doc.get("quotable", True)),
        "provenance_kind": doc.get("provenance_kind"),
        "risk_areas": list(doc.get("risk_areas", [])),
        "trigger_types": list(doc.get("trigger_types", [])),
        "jurisdictions": list(doc.get("jurisdictions", [])),
    }


def precedent_metadata(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The ``SearchHit.metadata`` shape for a ``precedent_cases`` hit."""
    return {
        "title": doc.get("title"),
        "outcome": doc.get("outcome"),
        "closed_date": doc.get("closed_date"),
        "change_types": list(doc.get("change_types", [])),
        "domains": list(doc.get("domains", [])),
        "band": doc.get("band"),
        "control_ratings": dict(doc.get("control_ratings", {})),
    }


def control_metadata(doc: Mapping[str, Any]) -> dict[str, Any]:
    """The ``SearchHit.metadata`` shape for a ``controls`` hit."""
    return {
        "domain": doc.get("domain"),
        "name": doc.get("name"),
        "catalogue_weight": doc.get("catalogue_weight"),
        "evidence_artifact": doc.get("evidence_artifact"),
        "last_tested": doc.get("last_tested"),
    }


def text_for(index: str, doc: Mapping[str, Any]) -> str:
    """The verbatim text a hit reports -- always from this document (the
    system of record), never from the vector store's own metadata copy.
    """
    if index == POLICY_CLAUSES:
        return str(doc.get("text", ""))
    if index == PRECEDENT_CASES:
        return str(doc.get("title", ""))
    return str(doc.get("name", ""))


def metadata_for(index: str, doc: Mapping[str, Any]) -> dict[str, Any]:
    """Dispatch to the per-index metadata shape."""
    if index == POLICY_CLAUSES:
        return clause_metadata(doc)
    if index == PRECEDENT_CASES:
        return precedent_metadata(doc)
    return control_metadata(doc)


__all__ = [
    "CONTROLS",
    "KNOWN_INDEXES",
    "POLICY_CLAUSES",
    "PRECEDENT_CASES",
    "Document",
    "InMemoryKeywordCollection",
    "KeywordCollection",
    "MongoKeywordCollection",
    "build_in_memory_collections",
    "clause_metadata",
    "control_metadata",
    "metadata_for",
    "mongo_filter",
    "precedent_metadata",
    "text_for",
]
