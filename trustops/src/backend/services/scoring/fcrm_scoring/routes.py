"""S2's HTTP surface: ``POST /score``, ``POST /simulate`` and ``GET /models/...``.

No route here ever accepts a pre-computed score, band or route from the
caller — every response is the engine's own output. This is rule 1 at the
transport boundary, the same way the schema already makes it true at the
payload boundary.

Every route needs a verified principal and an OPA allow (``authorize``,
installed as a *router-level* dependency so a route added later cannot be
left open by omission), and then its own ``require_roles`` list, which is
the matrix ``ops/opa/data/permissions.json`` encodes for ``service:
scoring``. The PO role appears in none of them: a score, band or route
never reaches a PO from this service, whatever the gateway does.
"""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Request
from fcrm_common.auth import require_roles
from fcrm_common.authz import opa_dependency
from fcrm_common.errors import NotFoundError, UpstreamUnavailableError
from fcrm_contracts.api.scoring import (
    RoutingRulesVersion,
    ScoreRequest,
    ScoreResponse,
    ScoringModelVersion,
)
from fcrm_contracts.api.scoring_simulate import SimulateRequest, SimulateResponse
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

from fcrm_scoring import routes_routing
from fcrm_scoring.engine import compute_score
from fcrm_scoring.simulate import CaseInputs, simulate
from fcrm_scoring.store import ConfigStore

logger = logging.getLogger(__name__)

#: Authenticate, then ask OPA for ``service: scoring`` on the matched route
#: template. 401 without a token, 403 on deny, 503 when OPA is unreachable
#: or not installed (fail closed).
authorize = opa_dependency("scoring")

router = APIRouter(tags=["scoring"], dependencies=[Depends(authorize)])

#: Who may ask for a score of record: H1 (service account) and the analyst's
#: own override/re-score path. Committee members read but never score.
_SCORE_ROLES = (Role.SERVICE, Role.ANALYST)
#: Who may read the scoring model and routing rules a case is pinned to.
_CONFIG_READ_ROLES = (Role.SERVICE, Role.ANALYST, Role.COMMITTEE)


def get_store(request: Request) -> ConfigStore:
    """The active-config store the app wired into ``app.state`` at
    startup (``lifespan.py``) — a FastAPI dependency so routes stay
    testable with `app.state.store` swapped for an in-memory fake.
    """
    store: ConfigStore = request.app.state.store
    return store


def get_case_inputs(request: Request) -> CaseInputs:
    """Where ``POST /simulate`` reads the case's current inputs; unwired is a
    503 (fail closed), never a guess.

    Raises:
        fcrm_common.errors.UpstreamUnavailableError: the app was built without
            the lifespan having wired the case inputs.
    """
    inputs: Optional[CaseInputs] = getattr(request.app.state, "case_inputs", None)
    if inputs is None:
        logger.error("simulate called before the case inputs were wired")
        raise UpstreamUnavailableError(
            "The preview is not wired to the case service.", dependency="case"
        )
    return inputs


@router.post("/simulate", response_model=SimulateResponse)
async def simulate_rating(
    body: SimulateRequest,
    _principal: Annotated[Principal, Depends(require_roles(Role.ANALYST))],
    store: ConfigStore = Depends(get_store),
    cases: CaseInputs = Depends(get_case_inputs),
) -> SimulateResponse:
    """What the case would score with one control at ``value``: both scores of
    the control's domain and of the composite, each with its band and boundary
    flag. The analyst's preview before an override; it stores nothing, decides
    nothing, and is the engine that scores the case of record over S1's current
    inputs (a planned control is never among them, rule 7).
    """
    view = await cases.score_request(body.case_id)
    model = await _model_for(store, view.request.scoring_v)
    return simulate(view, body.control_id, body.value, model)


@router.post("/score", response_model=ScoreResponse)
async def score(
    body: ScoreRequest,
    _principal: Annotated[Principal, Depends(require_roles(*_SCORE_ROLES))],
    store: ConfigStore = Depends(get_store),
) -> ScoreResponse:
    """Compute a composite score. Idempotent and pure given the same
    ``body`` and the active model matching ``body.scoring_v`` — called by
    H1 after assessment proposals land, by the review-item override path,
    and by nothing else (design A §3.1; ADR-07: no agent calls this).
    """
    model = await _model_for(store, body.scoring_v)
    return compute_score(body, model)


@router.get("/models/active", response_model=ScoringModelVersion)
async def active_model(
    _principal: Annotated[Principal, Depends(require_roles(*_CONFIG_READ_ROLES))],
    store: ConfigStore = Depends(get_store),
) -> ScoringModelVersion:
    """The scoring model version currently active for new scoring calls."""
    return await store.active_model()


@router.get("/models/{version}", response_model=ScoringModelVersion)
async def model_by_version(
    version: int,
    _principal: Annotated[Principal, Depends(require_roles(*_CONFIG_READ_ROLES))],
    store: ConfigStore = Depends(get_store),
) -> ScoringModelVersion:
    """A pinned scoring model version, for replaying an older case's
    score exactly (every case pins the version it was scored with).
    """
    return await _model_for(store, version)


@router.get("/routing/active", response_model=RoutingRulesVersion)
async def active_routing(
    _principal: Annotated[Principal, Depends(require_roles(*_CONFIG_READ_ROLES))],
    store: ConfigStore = Depends(get_store),
) -> RoutingRulesVersion:
    """The routing rules version currently active (R1-R6, D-19)."""
    return await store.active_routing()


async def _model_for(store: ConfigStore, version: int) -> ScoringModelVersion:
    model = await store.model(version)
    if model is None:
        raise NotFoundError(f"no scoring model version {version}")
    return model


router.include_router(routes_routing.router)
