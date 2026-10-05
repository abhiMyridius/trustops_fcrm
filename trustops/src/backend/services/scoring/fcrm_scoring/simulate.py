"""The analyst's "what if this control were rated x" preview (``POST /simulate``).

S2 fetches the case's current scoring inputs from S1 (the one builder of "the case
as it stands", ``GET /internal/cases/{id}/score-request``: the same request H1
scores and re-scores from, planned controls never in it) and scores them twice with
the engine that scores the case of record: once as they stand, once with the one
control at the candidate value. Nothing is stored and nothing is decided, and
because the engine is the same one the override would use, the preview cannot
disagree with the number the override then produces.

S2 stays free of case data of its own: ``/score`` still reads nothing, and this
route holds the S1 inputs only for the length of one call. S2 reaches S1 with a
service-account token (``fcrm_service``), the only role an ``/internal/`` route of S1
answers.
"""

from __future__ import annotations

import logging
from typing import Final, Optional, Protocol
from urllib.parse import quote

from fcrm_common.errors import NotFoundError, UpstreamUnavailableError
from fcrm_common.http import ServiceClient, Timeouts
from fcrm_contracts.api.case_internal_assessment import ScoreRequestView
from fcrm_contracts.api.scoring import (
    ControlInput,
    DomainInput,
    DomainResult,
    ScoreRequest,
    ScoreResponse,
    ScoringModelVersion,
)
from fcrm_contracts.api.scoring_simulate import SimulateResponse
from pydantic import SecretStr, ValidationError

from fcrm_scoring.engine import compute_score

logger = logging.getLogger(__name__)

#: One read of S1's score request; the preview is typed as the analyst edits a
#: rating, so a slow S1 is an error, not a wait.
CASE_TIMEOUTS: Final = Timeouts(connect=2.0, read=5.0, write=5.0, pool=5.0)
SCORE_REQUEST_PATH: Final = "/internal/cases/{case_id}/score-request"


class CaseInputs(Protocol):
    """Where the case's current scoring inputs come from."""

    async def score_request(self, case_id: str) -> ScoreRequestView:
        """The S2 request for ``case_id`` as it stands, with what was left out of
        it.
        """
        ...


class HttpCaseInputs:
    """S1 over HTTP, with S2's service token.

    Args:
        client: A :class:`ServiceClient` pointed at S1's base URL (including its
            mount prefix).
    """

    def __init__(self, client: ServiceClient) -> None:
        """Wrap ``client``; it is closed by whoever built it."""
        self._client = client

    async def score_request(self, case_id: str) -> ScoreRequestView:
        """``GET /internal/cases/{case_id}/score-request``.

        Raises:
            fcrm_common.errors.NotFoundError: S1 has no such case.
            fcrm_common.errors.ConflictError: S1 holds nothing to score yet.
            fcrm_common.errors.UpstreamTimeoutError: S1 did not answer in time.
            fcrm_common.errors.UpstreamUnavailableError: S1 is unreachable or
                answered with a body that breaks its contract.
        """
        body = await self._client.request_json(
            "GET",
            SCORE_REQUEST_PATH.format(case_id=quote(case_id, safe="")),
            timeout=CASE_TIMEOUTS,
        )
        try:
            return ScoreRequestView.model_validate(body)
        except ValidationError as exc:
            logger.error(
                "S1 answered the score request with a body that breaks its contract",
                extra={"case_id": case_id, "errors_count": exc.error_count()},
            )
            raise UpstreamUnavailableError(
                "case answered score-request with an unexpected body",
                dependency="case",
            ) from exc


class UnconfiguredCaseInputs:
    """What the service holds when nothing says where S1 is: every call is a
    503 naming the missing setting, never an answer from somewhere else.
    """

    async def score_request(self, case_id: str) -> ScoreRequestView:
        """Always refuses.

        Raises:
            fcrm_common.errors.UpstreamUnavailableError: always.
        """
        logger.error("simulate needs CASE_SERVICE_URL, which is not set")
        raise UpstreamUnavailableError(
            "The preview needs the case service, and CASE_SERVICE_URL is not set.",
            dependency="case",
        )


def _with_rating(request: ScoreRequest, control_id: str, rating: float) -> ScoreRequest:
    """``request`` with one control at ``rating``."""
    return request.model_copy(
        update={
            "domains": [
                DomainInput(
                    domain=domain.domain,
                    likelihood=domain.likelihood,
                    impact=domain.impact,
                    controls=[
                        ControlInput(
                            control_id=c.control_id,
                            domain=c.domain,
                            rating=rating if c.control_id == control_id else c.rating,
                            catalogue_weight=c.catalogue_weight,
                        )
                        for c in domain.controls
                    ],
                )
                for domain in request.domains
            ]
        }
    )


def _control(
    view: ScoreRequestView, control_id: str
) -> tuple[DomainInput, ControlInput]:
    """The domain and the scored control named ``control_id``.

    Raises:
        fcrm_common.errors.NotFoundError: it is not a scored control of the case
            (a control held only as planned is never scored, so there is nothing
            to preview).
    """
    for domain in view.request.domains:
        for control in domain.controls:
            if control.control_id == control_id:
                return domain, control
    why = (
        "it is held only as a planned control, which is never scored"
        if control_id in view.planned_control_ids
        else "the case has no such control"
    )
    raise NotFoundError(
        f"{control_id} is not a scored control of {view.case_id}: {why}.",
        control_id=control_id,
    )


def _domain_result(response: ScoreResponse, domain: DomainInput) -> DomainResult:
    return next(r for r in response.domains if r.domain is domain.domain)


def simulate(
    view: ScoreRequestView,
    control_id: str,
    value: float,
    model: ScoringModelVersion,
) -> SimulateResponse:
    """Score ``view`` as it stands and with ``control_id`` at ``value``.

    Pure: the inputs and the model are arguments, the engine is
    :func:`fcrm_scoring.engine.compute_score`.

    Raises:
        fcrm_common.errors.NotFoundError: ``control_id`` is not scored in the case.
    """
    domain, _ = _control(view, control_id)
    before = compute_score(view.request, model)
    after = compute_score(_with_rating(view.request, control_id, value), model)
    b, a = _domain_result(before, domain), _domain_result(after, domain)
    line = (
        f"CE {b.control_effectiveness:.1f} → {a.control_effectiveness:.1f} · "
        f"retention {b.retention:.2f} → {a.retention:.2f} · "
        f"residual {b.residual:.1f} → {a.residual:.1f} · "
        f"composite {before.composite:.1f} → {after.composite:.1f} · "
        f"Band {after.band.value}"
    )
    return SimulateResponse(
        domain=domain.domain,
        ce=(b.control_effectiveness, a.control_effectiveness),
        retention=(b.retention, a.retention),
        residual=(b.residual, a.residual),
        composite=(before.composite, after.composite),
        band=(int(before.band.value), int(after.band.value)),
        route=(before.route.value, after.route.value),
        boundary_flag=(before.boundary_flag, after.boundary_flag),
        boundary_distance=(before.boundary_distance, after.boundary_distance),
        line=line,
    )


def case_inputs_for(
    url: Optional[str], *, caller: str, secret: Optional[str | SecretStr]
) -> tuple[CaseInputs, Optional[ServiceClient]]:
    """The case inputs S2 should use and the client to close at shutdown.

    Args:
        url: S1's base URL including its mount prefix, or ``None``.
        caller: This service's name, the subject of its service token.
        secret: ``SERVICE_TOKEN_SECRET``.

    Returns:
        The inputs and the client that must be closed with the app; with no
        URL the inputs refuse every call and there is no client.
    """
    if not url:
        return UnconfiguredCaseInputs(), None
    client = ServiceClient(
        url.rstrip("/"), dependency="case", caller=caller, token_secret=secret
    )
    return HttpCaseInputs(client), client
