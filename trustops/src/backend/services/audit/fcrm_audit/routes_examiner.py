"""S4's examiner routes: ``POST /examiner/ask``, ``POST /examiner/answers``
(service only) and ``GET /examiner/answers/{question_id}``.

Asking is for people who may read a case's chain: an analyst or a committee
member. The PO and the observer are refused (OPA and :func:`require_roles`
agree), and so is the service account, because a question is a person's. The
answer comes back from H1 through the service-only route; both the answer and a
refusal are stored, and an answer that cites a record the chain does not hold is
refused with a 422 whoever posts it (:mod:`fcrm_audit.examiner`).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from fcrm_common.auth import require_roles
from fcrm_common.errors import UpstreamUnavailableError
from fcrm_contracts.api.audit_oversight import (
    ExaminerAnswerIn,
    ExaminerAnswerOut,
    ExaminerAskRequest,
    ExaminerPending,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

from fcrm_audit.examiner import ExaminerService
from fcrm_audit.routes import IdempotencyKeyHeader, authorize, checked_idempotency_key

router = APIRouter(
    prefix="/examiner", tags=["examiner"], dependencies=[Depends(authorize)]
)

#: Who may ask a question and read an answer: people who may read a chain.
_ASK_ROLES = (Role.ANALYST, Role.COMMITTEE)
#: Who may read an answer: the askers, and the service account.
_READ_ROLES = (Role.ANALYST, Role.COMMITTEE, Role.SERVICE)
#: Who posts an answer: H1, through the service account.
_ANSWER_ROLES = (Role.SERVICE,)


def get_examiner(request: Request) -> ExaminerService:
    """The examiner service ``lifespan.py`` (or a test) put on ``app.state``.

    Raises:
        UpstreamUnavailableError: Nothing wired it (a deployment fault, 503).
    """
    service: ExaminerService | None = getattr(request.app.state, "examiner", None)
    if service is None:
        raise UpstreamUnavailableError(
            "The examiner is not configured.", dependency="examiner"
        )
    return service


def _pending(pending: ExaminerPending) -> JSONResponse:
    return JSONResponse(status_code=202, content=pending.model_dump(mode="json"))


@router.post(
    "/ask",
    response_model=ExaminerAnswerOut,
    responses={
        202: {
            "model": ExaminerPending,
            "description": "Stored and announced; the answer is not here yet.",
        },
        404: {"description": "The case has no audit chain."},
        503: {"description": "The question could not be announced."},
    },
)
async def ask(
    body: ExaminerAskRequest,
    principal: Annotated[Principal, Depends(require_roles(*_ASK_ROLES))],
    service: Annotated[ExaminerService, Depends(get_examiner)],
) -> ExaminerAnswerOut | JSONResponse:
    """Ask the examiner a question about one case and wait for its answer.

    The examiner answers only from the case's audit chain and cites the records
    it used; when the records do not hold the answer it says so. The call waits
    up to a minute, then answers 202 with the question's id.
    """
    result = await service.ask(body, principal.sub)
    if isinstance(result, ExaminerPending):
        return _pending(result)
    return result


@router.post(
    "/answers",
    response_model=ExaminerAnswerOut,
    status_code=201,
    responses={
        200: {"model": ExaminerAnswerOut, "description": "A replay: already stored."},
        409: {"description": "A different answer is already stored."},
        422: {"description": "The answer cites a record the chain does not hold."},
    },
)
async def post_answer(
    body: ExaminerAnswerIn,
    response: Response,
    _principal: Annotated[Principal, Depends(require_roles(*_ANSWER_ROLES))],
    service: Annotated[ExaminerService, Depends(get_examiner)],
    idempotency_key: IdempotencyKeyHeader = None,
) -> ExaminerAnswerOut:
    """Store H1's answer to a question. Storing the same answer again is a
    replay (200, nothing written); a different one for the same question is a
    409. The ``Idempotency-Key`` header is validated; the question's id and the
    body's fingerprint are what make the call idempotent.
    """
    checked_idempotency_key(idempotency_key)
    stored = await service.answer(body)
    if stored.replayed:
        response.status_code = 200
    return stored.value


@router.get(
    "/answers/{question_id}",
    response_model=ExaminerAnswerOut,
    responses={202: {"model": ExaminerPending, "description": "Not answered yet."}},
)
async def get_answer(
    question_id: str,
    _principal: Annotated[Principal, Depends(require_roles(*_READ_ROLES))],
    service: Annotated[ExaminerService, Depends(get_examiner)],
) -> ExaminerAnswerOut | JSONResponse:
    """The answer to a question, or 202 while it has none."""
    result = await service.read(question_id)
    if isinstance(result, ExaminerPending):
        return _pending(result)
    return result
