"""The S3 policy-service client: the precedent lookup the intake core needs
(``POST /search`` over the ``precedent_cases`` index, design A §6.2).

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

from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.http import ServiceClient
from fcrm_contracts.api.policy import SearchHit, SearchRequest, SearchResponse

from fcrm_caseflow.clients import CALL_TIMEOUTS, parse_body

logger = logging.getLogger(__name__)

DEPENDENCY: Final = "policy"
SEARCH_PATH: Final = "/search"
PRECEDENT_INDEX: Final = "precedent_cases"


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
    """

    def __init__(self, client: ServiceClient) -> None:
        """Wrap ``client``; it is shared with nothing else."""
        self._client = client

    async def top_precedent(self, query: str) -> Optional[PrecedentHit]:
        """The single closest closed case for ``query``, or ``None`` when S3
        finds none.

        Raises:
            fcrm_common.errors.ValidationError: ``query`` is blank.
            fcrm_common.errors.FcrmError: S3 refused or was unreachable.
        """
        if not query.strip():
            raise FcrmValidationError("a precedent search needs a non-blank query")
        request = SearchRequest(index=PRECEDENT_INDEX, query=query, top_k=1)
        body = await self._client.request_json(
            "POST",
            SEARCH_PATH,
            json=request.model_dump(mode="json", exclude_none=True),
            timeout=CALL_TIMEOUTS,
        )
        response = parse_body(
            SearchResponse, body, dependency=DEPENDENCY, route=SEARCH_PATH
        )
        if not response.hits:
            return None
        hit = response.hits[0]
        closed = hit.metadata.get("closed_date")
        return PrecedentHit(
            case_id=hit.id,
            title=str(hit.metadata.get("title") or hit.text),
            similarity=similarity_of(hit),
            closed_date=str(closed) if closed else None,
        )
