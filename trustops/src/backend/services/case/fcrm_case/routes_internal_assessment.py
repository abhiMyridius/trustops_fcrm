"""S1's internal routes of the assessment half: what H1's assessment core pushes
into the analyst's views and reads back to score a case (lane I3-A).

Service-only at the same three layers as :mod:`fcrm_case.routes_internal`: this
router's ``require_roles(Role.SERVICE)`` (fail closed), OPA's
``case-any-internal-all-service`` rule, and the gateway, which answers 404 to any
``/internal/`` path from outside. Left out of the public OpenAPI document for the
same reason.

Writes (``PUT``): one view document per case (the score also appends a history
row), idempotent by the ``Idempotency-Key`` header (:mod:`fcrm_case
.assessment_store`). They write no activity row and publish no event: H1 shows
what the analyst should see through ``POST /internal/cases/{id}/events``, with
its own idempotency key, so a retried step cannot announce twice.

Reads (``GET``): ``score-request`` is the one place the S2 request is built from
the case's review items (:func:`fcrm_case.domain.assessment.build_score_view`), so
H1's scoring, H1's re-scoring and S2's simulate cannot disagree about what "the
case as it stands" means; ``evidence`` is what the case holds for one control.
"""

from __future__ import annotations

import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, Query, Request
from fcrm_common.auth import require_roles
from fcrm_common.errors import (
    NotFoundError,
    UpstreamUnavailableError,
    ValidationError,
)
from fcrm_contracts.api.case_internal_assessment import (
    CompletenessPush,
    CriticPush,
    HandoffPush,
    RecommendationDraftPush,
    ScorePush,
    ScoreRequestView,
    ScreeningPush,
    ViewWriteResult,
)
from fcrm_contracts.enums import ItemKind, Role
from fcrm_contracts.tools.evidence_store import EvidenceStoreOutput

from fcrm_case.assessment_store import AssessmentStore, checked_idempotency_key
from fcrm_case.deps import get_store, get_views
from fcrm_case.domain.assessment import build_score_view, control_evidence
from fcrm_case.store import CaseStore
from fcrm_case.views_store import ViewStore

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/internal",
    tags=["internal-assessment"],
    include_in_schema=False,
    dependencies=[Depends(require_roles(Role.SERVICE))],
)

IdempotencyKey = Annotated[Optional[str], Header(alias="Idempotency-Key")]


def get_assessment(request: Request) -> AssessmentStore:
    """The assessment store ``lifespan.register`` (or a test) put on
    ``app.state``.

    Raises:
        fcrm_common.errors.UpstreamUnavailableError: nothing wired one, a
            deployment fault answered 503 (fail closed), as
            :mod:`fcrm_case.deps` does for every other dependency.
    """
    store: Optional[AssessmentStore] = getattr(request.app.state, "assessment", None)
    if store is None:
        logger.error(
            "case service dependency not wired", extra={"dependency": "assessment"}
        )
        raise UpstreamUnavailableError(
            "The case service is not fully configured (assessment).",
            dependency="assessment",
        )
    return store


async def _require_case(store: CaseStore, case_id: str) -> None:
    if not await store.exists(case_id):
        raise NotFoundError(f"no such case {case_id}")


@router.put("/cases/{case_id}/screening")
async def put_screening(
    case_id: str,
    body: ScreeningPush,
    cases: Annotated[CaseStore, Depends(get_store)],
    assessment: Annotated[AssessmentStore, Depends(get_assessment)],
    idempotency_key: IdempotencyKey = None,
) -> ViewWriteResult:
    """The screening agent's R4 proposal and the gate that decides it."""
    await _require_case(cases, case_id)
    return await assessment.put_screening(
        case_id, body, checked_idempotency_key(idempotency_key)
    )


@router.put("/cases/{case_id}/score")
async def put_score(
    case_id: str,
    body: ScorePush,
    cases: Annotated[CaseStore, Depends(get_store)],
    assessment: Annotated[AssessmentStore, Depends(get_assessment)],
    idempotency_key: IdempotencyKey = None,
) -> ViewWriteResult:
    """One score of record, exactly as S2 answered. The body names the request
    S2 was given too, so a reader can see every number that went in.

    Raises:
        fcrm_common.errors.ValidationError: the body's request or response
            names another case.
    """
    if body.request.case_id != case_id or body.score.case_id != case_id:
        raise ValidationError("the score request and response must name this case")
    await _require_case(cases, case_id)
    result = await assessment.put_score(
        case_id, body, checked_idempotency_key(idempotency_key)
    )
    logger.info(
        "score snapshot stored",
        extra={
            "case_id": case_id,
            "run_id": body.run_id,
            "purpose": body.purpose,
            "replayed": result.replayed,
        },
    )
    return result


@router.put("/cases/{case_id}/critic")
async def put_critic(
    case_id: str,
    body: CriticPush,
    cases: Annotated[CaseStore, Depends(get_store)],
    assessment: Annotated[AssessmentStore, Depends(get_assessment)],
    idempotency_key: IdempotencyKey = None,
) -> ViewWriteResult:
    """The critic's findings."""
    await _require_case(cases, case_id)
    return await assessment.put_critic(
        case_id, body, checked_idempotency_key(idempotency_key)
    )


@router.put("/cases/{case_id}/completeness")
async def put_completeness(
    case_id: str,
    body: CompletenessPush,
    cases: Annotated[CaseStore, Depends(get_store)],
    assessment: Annotated[AssessmentStore, Depends(get_assessment)],
    idempotency_key: IdempotencyKey = None,
) -> ViewWriteResult:
    """The completeness report, the drafted questions and the ask/proceed gate."""
    await _require_case(cases, case_id)
    return await assessment.put_completeness(
        case_id, body, checked_idempotency_key(idempotency_key)
    )


@router.put("/cases/{case_id}/recommendation-draft")
async def put_recommendation_draft(
    case_id: str,
    body: RecommendationDraftPush,
    cases: Annotated[CaseStore, Depends(get_store)],
    assessment: Annotated[AssessmentStore, Depends(get_assessment)],
    idempotency_key: IdempotencyKey = None,
) -> ViewWriteResult:
    """The narrative agent's draft. Replaces the draft only: the analyst's edits
    and the lock, stored beside it, are never touched.
    """
    await _require_case(cases, case_id)
    return await assessment.put_recommendation_draft(
        case_id, body, checked_idempotency_key(idempotency_key)
    )


@router.put("/cases/{case_id}/handoff")
async def put_handoff(
    case_id: str,
    body: HandoffPush,
    cases: Annotated[CaseStore, Depends(get_store)],
    assessment: Annotated[AssessmentStore, Depends(get_assessment)],
    idempotency_key: IdempotencyKey = None,
) -> ViewWriteResult:
    """The pack H1 assembled from the locked numbers and its four release checks."""
    await _require_case(cases, case_id)
    return await assessment.put_handoff(
        case_id, body, checked_idempotency_key(idempotency_key)
    )


@router.get("/cases/{case_id}/score-request")
async def get_score_request(
    case_id: str,
    cases: Annotated[CaseStore, Depends(get_store)],
    views: Annotated[ViewStore, Depends(get_views)],
) -> ScoreRequestView:
    """Everything S2 needs to score the case as it stands: its current factor
    and control items at their effective values, at the case's pinned scoring
    version. A planned control is never in it.

    Raises:
        fcrm_common.errors.NotFoundError: no such case.
        fcrm_common.errors.ConflictError: the assessment has not produced a
            likelihood and impact yet, or an item is malformed.
    """
    record = await cases.get(case_id, with_versions=False)
    if record is None:
        raise NotFoundError(f"no such case {case_id}")
    rows = await views.list_items(case_id)
    return build_score_view(case_id, record.pinned_versions.scoring_v, rows)


@router.get("/cases/{case_id}/evidence")
async def get_evidence(
    case_id: str,
    cases: Annotated[CaseStore, Depends(get_store)],
    views: Annotated[ViewStore, Depends(get_views)],
    control_id: Annotated[str, Query(min_length=1, max_length=64)],
) -> EvidenceStoreOutput:
    """What the case holds as evidence for one control (the ``evidence_store``
    tool's S1 half): the artefact and date each of the control's items names and
    the document it cites. Empty before the first assessment.
    """
    await _require_case(cases, case_id)
    rows = await views.list_items(case_id, ItemKind.CONTROL)
    return control_evidence(case_id, control_id, rows)
