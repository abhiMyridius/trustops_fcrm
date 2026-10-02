"""The ``policy_search`` and ``precedent_search`` tools both call S3's
generic hybrid search over one of the three S3 Vectors indexes
(``policy_clauses``, ``precedent_cases``, ``controls``) with metadata
filters — see :class:`fcrm_contracts.api.policy.SearchRequest` and
:class:`~fcrm_contracts.api.policy.SearchResponse`, which this module
re-exports under tool-specific names rather than duplicating the shape,
since the wire contract really is identical; only the fixed ``index``
value differs per tool (the agent spec, not this schema, pins it)."""

from __future__ import annotations

from fcrm_contracts.api.policy import (
    SearchFilters,
    SearchHit,
    SearchRequest,
    SearchResponse,
)

PolicySearchInput = SearchRequest
PolicySearchOutput = SearchResponse
PrecedentSearchInput = SearchRequest
PrecedentSearchOutput = SearchResponse

__all__ = [
    "PolicySearchInput",
    "PolicySearchOutput",
    "PrecedentSearchInput",
    "PrecedentSearchOutput",
    "SearchFilters",
    "SearchHit",
]
