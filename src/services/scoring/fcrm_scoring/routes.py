"""S2's HTTP surface: ``POST /score`` and ``GET /models/...``.

No route here ever accepts a pre-computed score, band or route from the
caller — every response is the engine's own output. This is rule 1 at the
transport boundary, the same way the schema already makes it true at the
payload boundary.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fcrm_common.errors import NotFoundError
from fcrm_contracts.api.scoring import (
    RoutingRulesVersion,
    ScoreRequest,
    ScoreResponse,
    ScoringModelVersion,
)

from fcrm_scoring.engine import compute_score
from fcrm_scoring.store import ConfigStore

router = APIRouter(tags=["scoring"])


def get_store(request: Request) -> ConfigStore:
    """The active-config store the app wired into ``app.state`` at
    startup (``lifespan.py``) — a FastAPI dependency so routes stay
    testable with `app.state.store` swapped for an in-memory fake.
    """
    store: ConfigStore = request.app.state.store
    return store


@router.post("/score", response_model=ScoreResponse)
async def score(
    body: ScoreRequest, store: ConfigStore = Depends(get_store)
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
    store: ConfigStore = Depends(get_store),
) -> ScoringModelVersion:
    """The scoring model version currently active for new scoring calls."""
    return await store.active_model()


@router.get("/models/{version}", response_model=ScoringModelVersion)
async def model_by_version(
    version: int, store: ConfigStore = Depends(get_store)
) -> ScoringModelVersion:
    """A pinned scoring model version, for replaying an older case's
    score exactly (every case pins the version it was scored with).
    """
    return await _model_for(store, version)


@router.get("/routing/active", response_model=RoutingRulesVersion)
async def active_routing(
    store: ConfigStore = Depends(get_store),
) -> RoutingRulesVersion:
    """The routing rules version currently active (R1-R6, D-19)."""
    return await store.active_routing()


async def _model_for(store: ConfigStore, version: int) -> ScoringModelVersion:
    model = await store.model(version)
    if model is None:
        raise NotFoundError(f"no scoring model version {version}")
    return model
