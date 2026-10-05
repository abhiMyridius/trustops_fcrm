"""The product owner's HTTP surface (scenes 1-7), in the shapes the web's PO
screens read (ADR-30: the web's route and type names are the contract).

This module is deliberately thin and deliberately blind. It imports only the PO
contract (``fcrm_contracts.api.case_po``), the platform's common pieces and the
service that does the work; ``tests/unit/services/case/test_po_router_imports.py``
fails if it ever imports anything that holds analyst, score or harness data.
Every route:

* authenticates, and asks OPA whether the caller's role may call it (fail
  closed: OPA down is a 503, never an allow);
* requires the PO role, and the service requires the caller to own the case
  (403 on a write, 404 on a read);
* answers through :class:`fcrm_case.po_guard.PoGuardedRoute`, which re-scans the
  JSON for restricted keys and terms and replaces it with 500
  ``rule_violation:R3`` when it finds one.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Final, Literal, Optional

from fastapi import APIRouter, Depends, Query, Request, Response
from fcrm_common.auth import require_roles
from fcrm_common.errors import ValidationError
from fcrm_contracts.api.case_po import (
    ActivityEventPO,
    AnswersBody,
    CasePO,
    CasePOListItem,
    CategoryDecisionBody,
    CreateCaseBody,
    DashboardPO,
    DemoFilesBody,
    DiscardBody,
    DocumentsViewPO,
    EmptyBody,
    IntakeViewPO,
    Integration,
    IntegrationActionBody,
    POStage,
    ReuseBody,
    ReviewViewPO,
    SubmitResult,
    ValueDecisionBody,
)
from fcrm_contracts.common import Page, Principal
from fcrm_contracts.enums import ChangeType, Role
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

from fcrm_case.deps import authorize_case, get_po_service, get_settings
from fcrm_case.po_documents import IncomingFile, read_limited
from fcrm_case.po_errors import FileRefusedError, PayloadTooLargeError
from fcrm_case.po_guard import PoGuardedRoute
from fcrm_case.po_service import PoService
from fcrm_case.settings import CaseSettings

logger = logging.getLogger(__name__)

#: ``GET /cases`` and ``GET .../activity`` page bounds (the same shape as every list).
DEFAULT_PAGE_LIMIT: Final = 50
MAX_PAGE_LIMIT: Final = 200
#: Headroom over the files themselves for the multipart framing.
_MULTIPART_OVERHEAD: Final = 1024 * 1024

router = APIRouter(
    tags=["po"], route_class=PoGuardedRoute, dependencies=[Depends(authorize_case)]
)

Owner = Annotated[Principal, Depends(require_roles(Role.PO))]
Service = Annotated[PoService, Depends(get_po_service)]
Settings = Annotated[CaseSettings, Depends(get_settings)]
PageLimit = Annotated[int, Query(ge=1, le=MAX_PAGE_LIMIT)]


def _parse(model: type[BaseModel], payload: Any) -> Any:
    """``payload`` validated as ``model``; a bad body is a 422 naming each field."""
    try:
        return model.model_validate(payload)
    except PydanticValidationError as exc:
        raise ValidationError(
            "The request body is not valid.",
            errors=[
                {
                    "loc": ".".join(str(p) for p in err["loc"]),
                    "msg": str(err["msg"]),
                }
                for err in exc.errors()
            ],
        ) from exc


# -- dashboard, cases ------------------------------------------------------------


@router.get("/dashboard")
async def dashboard(principal: Owner, service: Service) -> DashboardPO:
    """The owner's KPIs and open cases."""
    return await service.dashboard(principal)


@router.get("/cases")
async def list_cases(
    principal: Owner,
    service: Service,
    stage: Optional[POStage] = None,
    needs_response: bool = False,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: Optional[str] = None,
) -> Page[CasePOListItem]:
    """The owner's cases, newest first; ``stage`` and ``needs_response`` filter."""
    return await service.list_cases(
        principal,
        stage=stage,
        needs_response=needs_response,
        limit=limit,
        cursor=cursor,
    )


@router.post("/cases", status_code=201)
async def create_case(
    principal: Owner, service: Service, body: Optional[CreateCaseBody] = None
) -> CasePO:
    """Raise a draft case: the id is minted and the versions in force pinned."""
    return await service.create_case(principal, body or CreateCaseBody())


@router.get("/cases/{case_id}")
async def get_case(case_id: str, principal: Owner, service: Service) -> CasePO:
    """The case header, stage and what the owner does next."""
    return await service.get_case(principal, case_id)


# -- documents -------------------------------------------------------------------


@router.get("/cases/{case_id}/documents")
async def get_documents(
    case_id: str, principal: Owner, service: Service
) -> DocumentsViewPO:
    """The Documents tab."""
    return await service.documents(principal, case_id)


async def _multipart_files(
    request: Request, settings: CaseSettings
) -> list[IncomingFile]:
    """The ``files`` parts of a multipart upload, each read under the size cap."""
    announced = request.headers.get("content-length")
    ceiling = settings.upload_max_bytes * settings.upload_max_files
    if (
        announced is not None
        and announced.isdigit()
        and int(announced) > ceiling + _MULTIPART_OVERHEAD
    ):
        raise PayloadTooLargeError("The upload is over the size limit.")
    form = await request.form(max_files=settings.upload_max_files, max_fields=20)
    try:
        parts = form.getlist("files")
        files: list[IncomingFile] = []
        for part in parts:
            if isinstance(part, str):
                raise FileRefusedError("Send files in the 'files' field.")
            name = part.filename or ""
            data = await read_limited(
                part, name=name or "file", max_bytes=settings.upload_max_bytes
            )
            files.append(IncomingFile(name, data))
        return files
    finally:
        await form.close()


@router.post(
    "/cases/{case_id}/documents",
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "files": {
                                "type": "array",
                                "items": {"type": "string", "format": "binary"},
                            }
                        },
                        "required": ["files"],
                    }
                },
                "application/json": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "demo_files": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "name": {"type": "string"},
                                        "size": {"type": "integer"},
                                    },
                                    "required": ["name"],
                                },
                            }
                        },
                    }
                },
            },
        }
    },
)
async def upload_documents(
    case_id: str,
    request: Request,
    principal: Owner,
    service: Service,
    settings: Settings,
) -> DocumentsViewPO:
    """Upload files (multipart ``files``), or use the committed demo documents
    (JSON ``{"demo_files": [...]}``). Each file is checked, stored and announced
    to Caseflow in one transaction.
    """
    await service.check_upload(principal, case_id)
    kind = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if kind == "multipart/form-data":
        files = await _multipart_files(request, settings)
        return await service.upload(principal, case_id, files)
    if kind == "application/json":
        try:
            payload = await request.json()
        except ValueError as exc:
            raise ValidationError("The request body is not valid JSON.") from exc
        body: DemoFilesBody = _parse(DemoFilesBody, payload)
        return await service.upload_demo(
            principal, case_id, [f.name for f in body.demo_files]
        )
    raise ValidationError(
        "Send multipart/form-data with 'files', or JSON with 'demo_files'."
    )


# -- intake ----------------------------------------------------------------------


@router.get("/cases/{case_id}/intake")
async def get_intake(case_id: str, principal: Owner, service: Service) -> IntakeViewPO:
    """The Overview tab: proposed types, values with sources, precedent, questions."""
    return await service.intake(principal, case_id)


@router.post("/cases/{case_id}/intake/categories/{change_type}")
async def decide_category(
    case_id: str,
    change_type: ChangeType,
    body: CategoryDecisionBody,
    principal: Owner,
    service: Service,
) -> IntakeViewPO:
    """Confirm or remove one proposed change type (a removal needs a reason)."""
    return await service.decide_category(principal, case_id, change_type, body)


@router.post("/cases/{case_id}/intake/values/{field_id}")
async def decide_value(
    case_id: str,
    field_id: str,
    body: ValueDecisionBody,
    principal: Owner,
    service: Service,
) -> ReviewViewPO:
    """Confirm or correct one value; a correction keeps the original."""
    return await service.decide_value(principal, case_id, field_id, body)


@router.post("/cases/{case_id}/intake/reuse")
async def reuse_values(
    case_id: str, body: ReuseBody, principal: Owner, service: Service
) -> IntakeViewPO:
    """Reuse values from the precedent the assistant found."""
    return await service.reuse(principal, case_id, body)


@router.post("/cases/{case_id}/intake/answers")
async def answer_questions(
    case_id: str, body: AnswersBody, principal: Owner, service: Service
) -> IntakeViewPO:
    """Answer the assistant's questions (one decision per question)."""
    return await service.answer(principal, case_id, body)


# -- review and submit -----------------------------------------------------------


@router.get("/cases/{case_id}/review")
async def get_review(case_id: str, principal: Owner, service: Service) -> ReviewViewPO:
    """The Review & submit tab."""
    return await service.review(principal, case_id)


@router.post("/cases/{case_id}/validate")
async def validate_case(
    case_id: str,
    principal: Owner,
    service: Service,
    body: Optional[EmptyBody] = None,
) -> SubmitResult:
    """A dry run of the four submit checks (always 200)."""
    return await service.validate(principal, case_id)


@router.post("/cases/{case_id}/submit")
async def submit_case(
    case_id: str,
    principal: Owner,
    service: Service,
    body: Optional[EmptyBody] = None,
) -> SubmitResult:
    """Run the four checks again and submit; 422 names the failing check."""
    return await service.submit(principal, case_id)


@router.post("/cases/{case_id}/discard", status_code=204)
async def discard_case(
    case_id: str, body: DiscardBody, principal: Owner, service: Service
) -> Response:
    """Discard an unsubmitted draft, with a reason."""
    await service.discard(principal, case_id, body.reason)
    return Response(status_code=204)


# -- activity, integrations ------------------------------------------------------


@router.get("/cases/{case_id}/activity")
async def get_activity(
    case_id: str,
    principal: Owner,
    service: Service,
    limit: PageLimit = DEFAULT_PAGE_LIMIT,
    cursor: Optional[str] = None,
) -> Page[ActivityEventPO]:
    """The owner's activity feed, newest first, in plain words."""
    return await service.activity(principal, case_id, limit=limit, cursor=cursor)


@router.get("/integrations")
async def list_integrations(principal: Owner, service: Service) -> Page[Integration]:
    """The owner's integrations and their choices."""
    return await service.integrations(principal)


@router.post("/integrations/{integration_id}/{action}")
async def set_integration(
    integration_id: str,
    action: Literal["connect", "disconnect"],
    principal: Owner,
    service: Service,
    body: Optional[IntegrationActionBody] = None,
) -> Integration:
    """Connect an integration to a location or channel, or disconnect it."""
    return await service.set_integration(
        principal, integration_id, action, body or IntegrationActionBody()
    )
