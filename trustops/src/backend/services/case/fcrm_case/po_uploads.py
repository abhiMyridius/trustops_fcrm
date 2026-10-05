"""Storing what the product owner uploads: check each file, put it in the object
store under ``uploads/{case}/{doc}/{name}``, and describe the rows, events and
activity that record it. The caller commits them in ONE transaction
(:meth:`fcrm_case.po_store.PoStore.commit`), so a document row and its
``case.document_stored`` event exist together or not at all.

The objects are written first, in parallel, and the database second: a failure
before the commit leaves objects no row points at (named in the log, harmless:
the keys are unique) and nothing else, which is the safe order. The event
carries every field H1 needs to start ingestion (bucket, key, sha256, the terms
Guard 1 must not mask, the round that asked for it), because the engine rejects a
partial payload.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final, Protocol

from fcrm_common.errors import FcrmError
from fcrm_common.events import build_envelope
from fcrm_common.ids import new_id
from fcrm_contracts.api.case_internal import UiActor, UiEventIn
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import CaseDocumentStored

from fcrm_case.domain.po_commands import ActivityDraft
from fcrm_case.domain.record import CaseRecord
from fcrm_case.domain.views import DocumentRow
from fcrm_case.po_documents import (
    DEMO_INTAKE_FILES,
    DemoFixtures,
    IncomingFile,
    check_file,
    clean_file_name,
)
from fcrm_case.po_errors import FileRefusedError, PayloadTooLargeError
from fcrm_case.po_text import reject_reserved_terms
from fcrm_case.settings import CaseSettings
from fcrm_case.views_store import MAX_DOCUMENTS_PER_CASE

logger = logging.getLogger(__name__)

_PO_ACTOR_COMPONENT: Final = "S1"


class ObjectWriter(Protocol):
    """What storing an upload needs of the object store."""

    async def put_bytes(
        self, bucket: str, key: str, data: bytes, content_type: str
    ) -> Any:
        """Store ``data`` at ``bucket``/``key``."""
        ...


@dataclass(frozen=True)
class StoredUpload:
    """The records of an upload whose objects are already stored: insert the
    ``rows`` and enqueue the ``events`` together.
    """

    rows: list[DocumentRow]
    events: list[Envelope]
    activity: list[ActivityDraft]


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def keep_terms_for(record: CaseRecord) -> list[str]:
    """Terms Guard 1 must not mask in this case's documents: the owner's name
    (field F01 depends on it) and the unit.
    """
    terms = [record.owner_name or "", record.unit]
    return [t for i, t in enumerate(terms) if t and t not in terms[:i]]


class DocumentIntake:
    """Validates, stores and describes a case's uploaded documents.

    Args:
        objects: The object store.
        fixtures: Where the committed demo documents are read from.
        settings: The upload limits and the uploads bucket.
    """

    def __init__(
        self, objects: ObjectWriter, fixtures: DemoFixtures, settings: CaseSettings
    ) -> None:
        self._objects = objects
        self._fixtures = fixtures
        self._settings = settings

    async def demo_files(
        self, names: Sequence[str], existing: Sequence[DocumentRow]
    ) -> list[IncomingFile]:
        """The demo documents named by ``names`` (the demo case's two intake
        documents when none is named), read from the fixtures and leaving out
        any the case already holds (a repeated request changes nothing).

        Raises:
            fcrm_common.errors.NotFoundError: A name is not a demo document.
            FileRefusedError: A name is not a usable file name.
        """
        wanted = [clean_file_name(n) for n in (names or DEMO_INTAKE_FILES)]
        held = {d.name.lower() for d in existing}
        fresh = [n for n in dict.fromkeys(wanted) if n.lower() not in held]
        return [IncomingFile(name, await self._fixtures.read(name)) for name in fresh]

    async def store(
        self,
        record: CaseRecord,
        existing: Sequence[DocumentRow],
        files: Sequence[IncomingFile],
        *,
        actor: str,
        now: datetime,
    ) -> StoredUpload:
        """Check ``files``, store their objects and describe the records.

        Raises:
            FileRefusedError: A file is not allowed, is empty or is not what its
                extension says, or its name is reserved.
            PayloadTooLargeError: Too many files, too many documents on the
                case, or a file over the size cap.
            fcrm_common.errors.UpstreamUnavailableError: The object store is
                unreachable.
        """
        if not files:
            raise FileRefusedError("No file was sent.")
        if len(files) > self._settings.upload_max_files:
            raise PayloadTooLargeError(
                f"At most {self._settings.upload_max_files} files per upload."
            )
        if len(existing) + len(files) > MAX_DOCUMENTS_PER_CASE:
            raise PayloadTooLargeError(
                f"A case holds at most {MAX_DOCUMENTS_PER_CASE} documents."
            )
        checked = [self._checked(f) for f in files]
        digests = await asyncio.gather(
            *(asyncio.to_thread(_digest, f.data) for f, _ in checked)
        )
        planned = [
            (f, ctype, digest, new_id("doc"))
            for (f, ctype), digest in zip(checked, digests, strict=True)
        ]
        bucket = self._settings.s3_bucket_uploads
        await self._put_all(record.case_id, bucket, planned)
        rows = [
            self._row(record, f, ctype, digest, doc_id, bucket, actor, now)
            for f, ctype, digest, doc_id in planned
        ]
        return StoredUpload(
            rows=rows,
            events=[self._event(record, row, actor) for row in rows],
            activity=[self._activity(row, actor) for row in rows],
        )

    def _checked(self, file: IncomingFile) -> tuple[IncomingFile, str]:
        name = clean_file_name(file.name)
        reject_reserved_terms(name, f"The file name {name}", loc="files")
        content_type = check_file(
            name,
            file.data,
            allowed=self._settings.upload_allowed_extensions_list,
            max_bytes=self._settings.upload_max_bytes,
        )
        return IncomingFile(name, file.data), content_type

    async def _put_all(
        self,
        case_id: str,
        bucket: str,
        planned: Sequence[tuple[IncomingFile, str, str, str]],
    ) -> None:
        keys = [f"{case_id}/{doc_id}/{f.name}" for f, _, _, doc_id in planned]
        try:
            await asyncio.gather(
                *(
                    self._objects.put_bytes(bucket, key, f.data, ctype)
                    for key, (f, ctype, _, _) in zip(keys, planned, strict=True)
                )
            )
        except FcrmError:
            logger.warning(
                "upload failed before the database was written; stored objects "
                "are unreferenced",
                extra={"case_id": case_id, "keys": keys},
                exc_info=True,
            )
            raise

    @staticmethod
    def _row(
        record: CaseRecord,
        file: IncomingFile,
        content_type: str,
        digest: str,
        doc_id: str,
        bucket: str,
        actor: str,
        now: datetime,
    ) -> DocumentRow:
        return DocumentRow(
            doc_id=doc_id,
            case_id=record.case_id,
            name=file.name,
            status="stored",
            bucket=bucket,
            key=f"{record.case_id}/{doc_id}/{file.name}",
            size_bytes=len(file.data),
            content_type=content_type,
            uploaded_at=now,
            sha256=digest,
            uploaded_by=actor,
            keep_terms=keep_terms_for(record),
        )

    @staticmethod
    def _event(record: CaseRecord, row: DocumentRow, actor: str) -> Envelope:
        payload = CaseDocumentStored(
            case_id=row.case_id,
            actor=actor,
            doc_id=row.doc_id,
            name=row.name,
            bucket=row.bucket,
            key=row.key,
            sha256=row.sha256 or "",
            size_bytes=row.size_bytes,
            content_type=row.content_type,
            raised_in_round=row.raised_in_round,
            keep_terms=row.keep_terms,
        )
        return build_envelope(
            "case.document_stored",
            payload.model_dump(mode="json"),
            case_id=row.case_id,
            run_id=record.run_id,
        )

    @staticmethod
    def _activity(row: DocumentRow, actor: str) -> ActivityDraft:
        return ActivityDraft(
            UiEventIn(
                type="document.stored",
                visibility="po",
                summary=f"You uploaded {row.name}",
                detail={"name": row.name, "doc_id": row.doc_id},
                actor=UiActor(id=actor, role="fcrm_po", component=_PO_ACTOR_COMPONENT),
            )
        )
