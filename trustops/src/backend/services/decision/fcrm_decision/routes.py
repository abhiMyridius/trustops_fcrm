"""S5's HTTP surface, in the web's route names (ADR-30): frozen pack snapshots,
hidden votes, the 2-of-3 tally, release checks and the R6 committee-question
path.

Three modules hold the routes, split by who calls them:

* :mod:`fcrm_decision.routes_committee`: the queue, a case's packs, the votes,
  the threads and the case outcome (committee, analyst, service reads; the
  committee's two writes).
* :mod:`fcrm_decision.routes_analyst`: ask for or build a pack, release it,
  answer a thread.
* :mod:`fcrm_decision.routes_service`: supersede, confirm a re-vote, record an
  outcome (H1).

They are included here, under one router carrying the authorization dependency,
so a route added to any of them cannot be left open: every route needs a
verified principal and an OPA allow for ``service: decision`` on its matched
route template (401 / 403 / 503 fail-closed), then its own ``require_roles``
list (the matrix ``ops/opa/data/permissions.json`` encodes).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from fcrm_decision.deps import authorize
from fcrm_decision.routes_analyst import router as analyst_router
from fcrm_decision.routes_committee import router as committee_router
from fcrm_decision.routes_service import router as service_router

router = APIRouter(tags=["decision"], dependencies=[Depends(authorize)])
router.include_router(committee_router)
router.include_router(analyst_router)
router.include_router(service_router)
