"""The source preview behind every citation chip (ADR-76): one page of a
document's anchored text with the cited lines marked, and the original upload
for the browser to show inline.

Both read what already exists. H3 wrote the anchored Markdown
(``canonical/<case>/<doc>.anchored.md``, lines ``p.N l.M: text``, the same
file ``document_reader`` gives the agents) and S1 stored the upload at
``uploads/<case>/<doc>/<name>``; nothing is derived, cached or written here,
and reading a document is not a state change, so no audit record is made.

Who may look: the owner of the case (it is their own upload, the one thing the
fence lets them see) and an analyst, the same roles as
``GET /cases/{id}/documents``. A product owner asking about a case they do not
own gets the same 404 as every other owner read, so the case's existence is
not revealed.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Final, Optional, Protocol
from urllib.parse import quote

from fcrm_common.errors import NotFoundError, ValidationError
from fcrm_contracts.api.case_po import (
    DocumentHighlightPO,
    DocumentLinePO,
    DocumentPagePO,
)
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role

from fcrm_case.domain.views import DocumentRow
from fcrm_case.store import CaseStore
from fcrm_case.views_store import ViewStore

logger = logging.getLogger(__name__)

#: ``p.N l.M: text``, as :func:`fcrm_tools.markdown.render_anchored` writes it
#: (S1 does not depend on the tools package, so the pattern is restated here).
_ANCHOR: Final = re.compile(r"^p\.(\d+) l\.(\d+): (.*)$")
#: Largest anchored file served (the same ceiling ``document_reader`` reads under).
MAX_ANCHORED_BYTES: Final = 8 * 1024 * 1024
#: Lines of context either side of a cited line when the caller asks for a window.
DEFAULT_CONTEXT: Final = 6
MAX_CONTEXT: Final = 200
_ASCII_FALLBACK: Final = "document"


class ObjectReader(Protocol):
    """What the preview needs of the object store."""

    async def get_bytes(self, bucket: str, key: str) -> bytes:
        """The whole object at ``bucket``/``key``."""
        ...


@dataclass(frozen=True)
class DocumentFile:
    """The original upload, ready to send: its bytes, type and file name."""

    name: str
    content_type: str
    data: bytes

    @property
    def content_disposition(self) -> str:
        """``inline`` so the browser shows a PDF in the tab, with the name in
        both the ASCII and the RFC 5987 form.
        """
        ascii_name = (
            self.name.encode("ascii", "ignore").decode("ascii").replace('"', "")
        )
        stem, dot, ext = ascii_name.rpartition(".")
        if not dot:
            stem, ext = ext, ""
        if not stem.strip():
            ascii_name = f"{_ASCII_FALLBACK}.{ext}" if ext.strip() else _ASCII_FALLBACK
        return (
            f'inline; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(self.name, safe='')}"
        )


def parse_anchored(markdown: str) -> dict[int, list[DocumentLinePO]]:
    """``{page: [line, ...]}`` from anchored Markdown, each page's lines in
    anchored order (a gap in the numbering is left out, not filled)."""
    pages: dict[int, dict[int, str]] = {}
    for raw in markdown.splitlines():
        match = _ANCHOR.match(raw)
        if match is None:
            continue
        page, line, text = int(match.group(1)), int(match.group(2)), match.group(3)
        pages.setdefault(page, {})[line] = text
    return {
        page: [DocumentLinePO(line=n, text=t) for n, t in sorted(lines.items())]
        for page, lines in sorted(pages.items())
    }


class DocumentPreviewService:
    """Reads for the source preview over the case store, the view store and
    the object store ``lifespan.py`` wired.

    Args:
        cases: The case records (ownership, discarded drafts).
        views: The document rows (status, object keys, page count).
        objects: The object store holding the uploads and the anchored text.
        uploads_bucket: Where S1 stored the uploads.
        canonical_bucket: Where H3 wrote the anchored Markdown.
    """

    def __init__(
        self,
        *,
        cases: CaseStore,
        views: ViewStore,
        objects: ObjectReader,
        uploads_bucket: str,
        canonical_bucket: str,
    ) -> None:
        self._cases = cases
        self._views = views
        self._objects = objects
        self._uploads_bucket = uploads_bucket
        self._canonical_bucket = canonical_bucket

    async def page(
        self,
        principal: Principal,
        case_id: str,
        doc_id: str,
        *,
        page: int,
        line: Optional[int] = None,
        line_end: Optional[int] = None,
        context: Optional[int] = None,
    ) -> DocumentPagePO:
        """Page ``page`` of the document's anchored text. With ``line`` the
        span ``line..line_end`` (``line_end`` defaults to ``line``) is marked,
        and with ``context`` only that many lines either side of it are sent.

        Raises:
            NotFoundError: No such case or document for this caller, the page
                is outside the document, or the document has no extracted
                text (not yet read, quarantined or failed).
            ValidationError: ``line_end`` before ``line``, or ``context``
                without ``line``.
            UpstreamUnavailableError: The object store is unreachable.
        """
        if context is not None and line is None:
            raise ValidationError("'context' needs 'line'.")
        if line is not None and line_end is not None and line_end < line:
            raise ValidationError("'line_end' must not be before 'line'.")
        row = await self._document(principal, case_id, doc_id)
        pages = parse_anchored(await self._anchored_text(row))
        if not pages:
            raise NotFoundError(f"Document {doc_id} has no readable text.")
        page_count = row.pages or max(pages)
        if not 1 <= page <= page_count:
            raise NotFoundError(f"Document {doc_id} has pages 1 to {page_count}.")
        lines = pages.get(page, [])
        highlight: Optional[DocumentHighlightPO] = None
        if line is not None:
            highlight = DocumentHighlightPO(line_start=line, line_end=line_end or line)
            if context is not None:
                low = highlight.line_start - context
                high = highlight.line_end + context
                lines = [entry for entry in lines if low <= entry.line <= high]
        return DocumentPagePO(
            doc_id=row.doc_id,
            name=row.name,
            page=page,
            pages=page_count,
            lines=lines,
            highlight=highlight,
        )

    async def file(
        self, principal: Principal, case_id: str, doc_id: str
    ) -> DocumentFile:
        """The original upload, as stored.

        Raises:
            NotFoundError: No such case or document for this caller, or the
                stored object is gone.
            UpstreamUnavailableError: The object store is unreachable.
        """
        row = await self._document(principal, case_id, doc_id)
        data = await self._objects.get_bytes(
            row.bucket or self._uploads_bucket, row.key
        )
        return DocumentFile(name=row.name, content_type=row.content_type, data=data)

    async def _document(
        self, principal: Principal, case_id: str, doc_id: str
    ) -> DocumentRow:
        record = await self._cases.get(case_id, with_versions=False)
        if record is None or record.discarded_at is not None:
            raise NotFoundError(f"no such case {case_id}")
        if Role.PO in principal.roles and record.owner != principal.sub:
            raise NotFoundError(f"no such case {case_id}")
        row = await self._views.get_document(doc_id)
        if row is None or row.case_id != case_id:
            raise NotFoundError(f"no such document {doc_id} on case {case_id}")
        return row

    async def _anchored_text(self, row: DocumentRow) -> str:
        if row.status != "extracted" or not row.anchored_key:
            raise NotFoundError(
                f"Document {row.doc_id} has no extracted text ({row.status})."
            )
        try:
            data = await self._objects.get_bytes(
                self._canonical_bucket, row.anchored_key
            )
        except NotFoundError as exc:
            logger.error(
                "anchored text missing for an extracted document",
                extra={"doc_id": row.doc_id, "case_id": row.case_id},
            )
            raise NotFoundError(
                f"The extracted text of document {row.doc_id} is missing."
            ) from exc
        if len(data) > MAX_ANCHORED_BYTES:
            raise NotFoundError(
                f"The extracted text of document {row.doc_id} is too large to show."
            )
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise NotFoundError(
                f"The extracted text of document {row.doc_id} is not readable."
            ) from exc
