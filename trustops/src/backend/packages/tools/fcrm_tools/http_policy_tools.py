"""The HTTP tools that read S3's policy data: ``policy_search`` (the clause
corpus) and ``control_inventory`` (the control catalogue).

Both are *pinned* reads: the case names the corpus version and the controls
version it was started on, S3 answers only for those, and a different version
coming back is a :class:`~fcrm_tools.errors.ToolScopeError`, never silently
served. What a tool read for a pinned version cannot change, so it is cached
for :data:`~fcrm_tools.http_common.FIELD_SET_TTL_S` in the registry's TTL cache
(design B §3.5: a re-run reuses what its changed fields did not touch).

``policy_search`` returns S3's hits with the text of each clause exactly as S3
holds it (policy is quoted verbatim, never generated), and **fails closed on a
hit that cannot be cited**: a clause without its source id or reference is an
:class:`~fcrm_common.errors.UpstreamUnavailableError`, and ``quotable`` is true
only when S3 says ``True`` (a catalogue entry is never quotable).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final

from fcrm_common.cache import TTLCache, cache_get, cache_set
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.http import ServiceClient
from fcrm_contracts.api.policy import (
    ControlInventoryOut,
    SearchHit,
    SearchResponse,
)
from fcrm_contracts.tools.control_inventory import (
    ControlInventoryInput,
    ControlInventoryOutput,
)
from fcrm_contracts.tools.policy_search import PolicySearchInput, PolicySearchOutput

from fcrm_tools.base import BaseTool
from fcrm_tools.cache import input_hash
from fcrm_tools.errors import ToolInputError, ToolScopeError
from fcrm_tools.http_common import (
    FIELD_SET_TTL_S,
    POLICY_PEER,
    check_top_k,
    fetch_model,
)
from fcrm_tools.seed_data import POLICY_INDEX

logger = logging.getLogger(__name__)

#: The metadata a clause hit carries to the pack builders, copied as S3 gives
#: it (``quotable`` is rebuilt, see :func:`policy_metadata`).
_COPIED_KEYS: Final = (
    "clause_id",
    "corpus_version",
    "title",
    "page",
    "line_start",
    "line_end",
    "provenance_kind",
    "risk_areas",
    "trigger_types",
    "jurisdictions",
    "vector_score",
    "text_score",
)


def policy_metadata(metadata: Mapping[str, Any]) -> dict[str, Any]:
    """S3's ``policy_clauses`` hit metadata as the pack builders read it.

    Raises:
        UpstreamUnavailableError: The hit has no ``source_id`` or ``ref``: a
            clause that cannot be cited is never offered to a model.
    """
    source_id, ref = metadata.get("source_id"), metadata.get("ref")
    if not source_id or not ref:
        logger.error(
            "S3 returned a clause hit with no citation",
            extra={"tool": "policy_search", "clause_id": metadata.get("clause_id")},
        )
        raise UpstreamUnavailableError(
            "policy returned a clause hit without its source and reference.",
            dependency=POLICY_PEER,
        )
    shaped: dict[str, Any] = {key: metadata.get(key) for key in _COPIED_KEYS}
    shaped["source_id"] = source_id
    shaped["ref"] = ref
    # Only an explicit True is quotable: a missing or odd value is a catalogue
    # entry, which a model may describe but never cite.
    shaped["quotable"] = metadata.get("quotable") is True
    return shaped


class HttpPolicySearchTool(BaseTool[PolicySearchInput, PolicySearchOutput]):
    """``policy_search``: the clauses of the pinned corpus most relevant to a
    query, from S3's ``policy_clauses`` index (keyword and semantic ranking
    fused), each hit carrying the clause's verbatim text and its citation.
    """

    name = "policy_search"
    input_model = PolicySearchInput
    output_model = PolicySearchOutput

    def __init__(self, policy: ServiceClient, cache: TTLCache) -> None:
        """Search through S3 (``policy``), caching pinned-version answers."""
        self._policy = policy
        self._cache = cache

    async def run(self, request: PolicySearchInput) -> PolicySearchOutput:
        """Top clauses for ``request.query`` under its filters.

        Raises:
            ToolInputError: another index, or ``top_k`` out of range.
            ToolScopeError: a hit is from a corpus version other than the one
                the request pins.
            UpstreamUnavailableError: a hit cannot be cited.
        """
        if request.index != POLICY_INDEX:
            msg = f"policy_search reads {POLICY_INDEX!r}, not {request.index!r}"
            raise ToolInputError(msg)
        check_top_k(request.top_k)
        pinned = request.filters.corpus_version
        key = f"policy_search:{input_hash(request)}" if pinned is not None else None
        if key is not None:
            cached = await cache_get(self._cache, key)
            if cached is not None:
                return SearchResponse.model_validate_json(cached)
        response = await fetch_model(
            self._policy,
            "/search",
            SearchResponse,
            tool=self.name,
            what=f"policy search {request.query[:40]!r}",
            method="POST",
            body=request.model_dump(mode="json"),
        )
        answer = SearchResponse(
            hits=[
                SearchHit(
                    id=hit.id,
                    score=hit.score,
                    text=hit.text,
                    metadata=policy_metadata(hit.metadata),
                )
                for hit in response.hits
            ]
        )
        self._check_pinned(answer, pinned)
        if key is not None:
            await cache_set(self._cache, key, answer.model_dump_json(), FIELD_SET_TTL_S)
        return answer

    @staticmethod
    def _check_pinned(answer: SearchResponse, pinned: int | None) -> None:
        """Every hit is from the pinned corpus version, when one is pinned."""
        if pinned is None:
            return
        stray = {
            hit.metadata["corpus_version"]
            for hit in answer.hits
            if hit.metadata["corpus_version"] != pinned
        }
        if stray:
            msg = f"corpus v{pinned} requested; S3 answered from {sorted(stray)}"
            raise ToolScopeError(msg)


class HttpControlInventoryTool(BaseTool[ControlInventoryInput, ControlInventoryOutput]):
    """``control_inventory``: the controls of one risk domain at the pinned
    inventory version, with their catalogue weights, from S3."""

    name = "control_inventory"
    input_model = ControlInventoryInput
    output_model = ControlInventoryOutput

    def __init__(self, policy: ServiceClient, cache: TTLCache) -> None:
        """Read from S3 through ``policy``, caching each (version, domain)."""
        self._policy = policy
        self._cache = cache

    async def run(self, request: ControlInventoryInput) -> ControlInventoryOutput:
        """The controls of ``request.domain`` at ``request.controls_v``.

        Raises:
            UnknownEntityError: S3 has no such inventory version.
            ToolScopeError: S3 answered for another version or another domain.
        """
        pinned = f"v{request.controls_v}"
        domain = request.domain.value
        key = f"control_inventory:{pinned}:{domain}"
        cached = await cache_get(self._cache, key)
        if cached is not None:
            return ControlInventoryOutput.model_validate_json(cached)
        inventory = await fetch_model(
            self._policy,
            "/controls",
            ControlInventoryOut,
            tool=self.name,
            what=f"control inventory {pinned}",
            params={"version": pinned, "domain": domain},
        )
        if inventory.version != pinned:
            msg = f"controls {pinned} requested; S3 answered {inventory.version}"
            raise ToolScopeError(msg)
        if any(control.domain != domain for control in inventory.controls):
            msg = f"controls of {domain} requested; S3 answered another domain too"
            raise ToolScopeError(msg)
        out = ControlInventoryOutput(controls=inventory.controls)
        await cache_set(self._cache, key, out.model_dump_json(), FIELD_SET_TTL_S)
        return out


__all__ = [
    "HttpControlInventoryTool",
    "HttpPolicySearchTool",
    "policy_metadata",
]
