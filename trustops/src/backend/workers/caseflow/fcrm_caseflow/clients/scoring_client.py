"""The S2 scoring-service client: ``POST /score``, the one way a score comes into
being (ADR-07: H1 calls it after the proposals land; no agent can).

S2's ``/score`` is a pure function of its body and the pinned model, so the call
is safe to repeat and carries an ``Idempotency-Key`` only so the shared client may
retry it on a 502/503/504. Its read timeout is 5 s (design A §10.3): an engine that
cannot answer in five seconds is down, and the step fails to be retried and parked,
never answered with a guess.
"""

from __future__ import annotations

import logging
from typing import Final, Optional

from fcrm_common.http import ServiceClient, Timeouts
from fcrm_contracts.api.scoring import ScoreRequest, ScoreResponse

from fcrm_caseflow.clients import parse_body

logger = logging.getLogger(__name__)

DEPENDENCY: Final = "scoring"
SCORE_PATH: Final = "/score"

#: ``/score`` read timeout 5 s (design A §10.3).
SCORE_TIMEOUTS: Final = Timeouts(connect=2.0, read=5.0, write=5.0, pool=5.0)


class ScoringClient:
    """Calls S2 on behalf of H1's steps.

    Args:
        client: A :class:`ServiceClient` already pointed at
            ``SCORING_SERVICE_URL``.
    """

    def __init__(self, client: ServiceClient) -> None:
        """Wrap ``client``; it is shared with nothing else."""
        self._client = client

    async def score(
        self, request: ScoreRequest, *, idempotency_key: Optional[str] = None
    ) -> ScoreResponse:
        """``POST /score``: the composite, band, route and boundary for
        ``request``, computed by the deterministic engine.

        Raises:
            fcrm_common.errors.ValidationError: S2 refused the request (422).
            fcrm_common.errors.NotFoundError: S2 has no scoring model at the
                request's pinned version.
            fcrm_common.errors.UpstreamTimeoutError: no answer within 5 s.
            fcrm_common.errors.UpstreamUnavailableError: S2 is unreachable or
                answered with a body that breaks its contract.
        """
        body = await self._client.request_json(
            "POST",
            SCORE_PATH,
            json=request.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            timeout=SCORE_TIMEOUTS,
        )
        return parse_body(ScoreResponse, body, dependency=DEPENDENCY, route=SCORE_PATH)
