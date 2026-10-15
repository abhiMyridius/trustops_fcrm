"""S1 document preview routes (ADR-76): the text behind a citation chip and
the original upload, for the owner of the case and the analyst.

These routes sit outside the PO router on purpose. ``routes_po`` re-scans
every owner response for restricted words, which is right for text the
system wrote and wrong for text the owner uploaded: the demo's own business
case says "inherent risk" on page 9. The fence still holds here by shape:
``DocumentPagePO`` (``fcrm_contracts.api.case_po``) carries only the
document's own lines and is locked by the forbidden-fields contract test, and
the file route returns the stored bytes untouched. Reading is not a state
change, so no audit record is written.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query, Response
from fcrm_common.auth import require_roles
from fcrm_contracts.api.case_po import DocumentPagePO
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

from fcrm_case.deps import authorize_case, get_document_preview
from fcrm_case.document_preview import MAX_CONTEXT, DocumentPreviewService

router = APIRouter(tags=["documents"], dependencies=[Depends(authorize_case)])

#: The same roles as ``GET /cases/{id}/documents``: the owner and an analyst.
Reader = Annotated[Principal, Depends(require_roles(Role.PO, Role.ANALYST))]
Preview = Annotated[DocumentPreviewService, Depends(get_document_preview)]


@router.get("/cases/{case_id}/documents/{doc_id}/pages")
async def get_document_page(
    case_id: str,
    doc_id: str,
    principal: Reader,
    preview: Preview,
    page: Annotated[int, Query(ge=1)] = 1,
    line: Annotated[Optional[int], Query(ge=1)] = None,
    line_end: Annotated[Optional[int], Query(ge=1)] = None,
    context: Annotated[Optional[int], Query(ge=0, le=MAX_CONTEXT)] = None,
) -> DocumentPagePO:
    """One page of the document's extracted text, as H3 anchored it
    (``p.N l.M``). ``line`` (and ``line_end``) mark the cited span;
    ``context`` trims the page to that many lines either side of it. 404 for
    a document that is not on the case, a page outside it, or a document with
    no extracted text; 503 when the object store is down.
    """
    return await preview.page(
        principal,
        case_id,
        doc_id,
        page=page,
        line=line,
        line_end=line_end,
        context=context,
    )


@router.get(
    "/cases/{case_id}/documents/{doc_id}/file",
    response_class=Response,
    responses={
        200: {"content": {"application/pdf": {}, "application/octet-stream": {}}}
    },
)
async def get_document_file(
    case_id: str, doc_id: str, principal: Reader, preview: Preview
) -> Response:
    """The original upload, sent ``inline`` with its stored content type so
    the browser shows a PDF in the tab (``#page=N`` opens the cited page).
    404 for a document that is not on the case; 503 when the object store is
    down.
    """
    file = await preview.file(principal, case_id, doc_id)
    return Response(
        content=file.data,
        media_type=file.content_type,
        headers={
            "Content-Disposition": file.content_disposition,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
