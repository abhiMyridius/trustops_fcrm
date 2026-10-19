"""The S3 policy-service client: the precedent lookup the intake core needs
(``POST /search`` over the ``precedent_cases`` index, design A §6.2), the same
search's raw hits the critic check reads ratings from, and the versioned
reference reads (control inventory, field set, jurisdiction list versions).

S3 answers with a fused rank, not a similarity: ``SearchHit.score`` is the
reciprocal-rank-fusion score divided by its ceiling, so the top hit of a
single-source index is always 1.0. The figure a person can read as
"similarity" travels in ``metadata`` (``vector_score``, else ``text_score``),
so :func:`similarity_of` prefers those.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Optional

from fcrm_common.cache import TTLCache, cache_get, cache_set
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.http import ServiceClient
from fcrm_contracts.api.policy import (
    ControlInventoryOut,
    FieldSetOut,
    JurisdictionVersionOut,
    SearchHit,
    SearchRequest,
    SearchResponse,
)
from fcrm_contracts.common import Page

from fcrm_caseflow.clients import CALL_TIMEOUTS, parse_body, path_segment

logger = logging.getLogger(__name__)

DEPENDENCY: Final = "policy"
SEARCH_PATH: Final = "/search"
PRECEDENT_INDEX: Final = "precedent_cases"
CONTROLS_PATH: Final = "/controls"
FIELD_SET_PATH: Final = "/field-sets/{version}"
JURISDICTION_VERSIONS_PATH: Final = "/jurisdictions/versions"

#: How long a control inventory or field set is reused. Both are versioned and a
#: case pins one version, so a stale read is never wrong, only older than it needs
#: to be; this just keeps a step from asking S3 for the same inventory twice.
REFERENCE_TTL_S: Final = 300


@dataclass(frozen=True)
class PrecedentHit:
    """The closest closed case S3 found.

    Attributes:
        case_id: The closed case.
        title: Its title as S3 indexed it.
        similarity: 0-1; see :func:`similarity_of`.
        closed_date: ISO date the case closed, when S3 has one.
    """

    case_id: str
    title: str
    similarity: float
    closed_date: Optional[str]


def similarity_of(hit: SearchHit) -> float:
    """The most meaningful 0-1 similarity ``hit`` carries.

    ``metadata["vector_score"]`` (H4's cosine) if present, else
    ``metadata["text_score"]`` (the keyword source's own score), else the
    fused ``score``. Each is clamped into [0, 1]: a Mongo ``textScore`` is not
    bounded.
    """
    for key in ("vector_score", "text_score"):
        value = hit.metadata.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return min(1.0, max(0.0, float(value)))
    return min(1.0, max(0.0, hit.score))


class PolicyClient:
    """Calls S3 on behalf of H1's steps.

    Args:
        client: A :class:`ServiceClient` already pointed at
            ``POLICY_SERVICE_URL``.
        cache: Where the versioned reference reads (:meth:`controls`,
            :meth:`field_set`) are kept; ``None`` reads S3 every time.
    """

    def __init__(
        self, client: ServiceClient, *, cache: Optional[TTLCache] = None
    ) -> None:
        """Wrap ``client``; it is shared with nothing else."""
        self._client = client
        self._cache = cache

    async def closest_precedents(
        self, query: str, top_k: int = 1
    ) -> list[PrecedentHit]:
        """The ``top_k`` closest closed cases for ``query``, closest first (an
        empty list when S3 finds none).

        Raises:
            fcrm_common.errors.ValidationError: ``query`` is blank.
            fcrm_common.errors.FcrmError: S3 refused or was unreachable.
        """
        found: list[PrecedentHit] = []
        for hit in await self.precedent_hits(query, top_k):
            closed = hit.metadata.get("closed_date")
            found.append(
                PrecedentHit(
                    case_id=hit.id,
                    title=str(hit.metadata.get("title") or hit.text),
                    similarity=similarity_of(hit),
                    closed_date=str(closed) if closed else None,
                )
            )
        return found

    async def precedent_hits(self, query: str, top_k: int) -> list[SearchHit]:
        """S3's ``top_k`` hits for ``query`` on the precedent index as S3 sends
        them (``metadata`` carries each closed case's ``control_ratings`` and
        ``closed_date``), closest first.

        Raises:
            fcrm_common.errors.ValidationError: ``query`` is blank.
            fcrm_common.errors.FcrmError: S3 refused or was unreachable.
        """
        if not query.strip():
            raise FcrmValidationError("a precedent search needs a non-blank query")
        request = SearchRequest(index=PRECEDENT_INDEX, query=query, top_k=top_k)
        body = await self._client.request_json(
            "POST",
            SEARCH_PATH,
            json=request.model_dump(mode="json", exclude_none=True),
            timeout=CALL_TIMEOUTS,
        )
        response = parse_body(
            SearchResponse, body, dependency=DEPENDENCY, route=SEARCH_PATH
        )
        return list(response.hits)

    async def top_precedent(self, query: str) -> Optional[PrecedentHit]:
        """The single closest closed case for ``query``, or ``None`` when S3
        finds none.

        Raises:
            fcrm_common.errors.ValidationError: ``query`` is blank.
            fcrm_common.errors.FcrmError: S3 refused or was unreachable.
        """
        hits = await self.closest_precedents(query, 1)
        return hits[0] if hits else None

    async def jurisdiction_versions(self) -> list[JurisdictionVersionOut]:
        """``GET /jurisdictions/versions``: every stored jurisdiction list version
        (version, publication date, fixture and active flags), not cached: a list
        is published while a round is in flight.

        Raises:
            fcrm_common.errors.FcrmError: S3 refused or was unreachable.
        """
        body = await self._client.request_json(
            "GET", JURISDICTION_VERSIONS_PATH, timeout=CALL_TIMEOUTS
        )
        page = parse_body(
            Page[JurisdictionVersionOut],
            body,
            dependency=DEPENDENCY,
            route=JURISDICTION_VERSIONS_PATH,
        )
        return page.items

    async def controls(self, version: str) -> ControlInventoryOut:
        """``GET /controls?version=``: the control inventory a case is pinned
        to (names and catalogue weights). Cached: the version is immutable.

        Raises:
            fcrm_common.errors.NotFoundError: S3 has no such version.
        """
        key = f"policy:controls:{version}"
        cached = await cache_get(self._cache, key)
        if cached is not None:
            return ControlInventoryOut.model_validate_json(cached)
        body = await self._client.request_json(
            "GET",
            CONTROLS_PATH,
            params={"version": version},
            timeout=CALL_TIMEOUTS,
        )
        inventory = parse_body(
            ControlInventoryOut, body, dependency=DEPENDENCY, route=CONTROLS_PATH
        )
        await cache_set(self._cache, key, inventory.model_dump_json(), REFERENCE_TTL_S)
        return inventory

    async def field_set(self, version: str) -> FieldSetOut:
        """``GET /field-sets/{version}``: the always-required fields and each
        change type's own, at the version a case is pinned to. Cached.

        Raises:
            fcrm_common.errors.NotFoundError: S3 has no such version.
        """
        key = f"policy:field_set:{version}"
        cached = await cache_get(self._cache, key)
        if cached is not None:
            return FieldSetOut.model_validate_json(cached)
        body = await self._client.request_json(
            "GET",
            FIELD_SET_PATH.format(version=path_segment(version)),
            timeout=CALL_TIMEOUTS,
        )
        field_set = parse_body(
            FieldSetOut, body, dependency=DEPENDENCY, route=FIELD_SET_PATH
        )
        await cache_set(self._cache, key, field_set.model_dump_json(), REFERENCE_TTL_S)
        return field_set
