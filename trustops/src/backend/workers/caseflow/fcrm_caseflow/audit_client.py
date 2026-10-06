"""The S4 audit-service client (design A §2.4: ``POST /records``).

Mirrors :mod:`fcrm_agent_worker.audit_client` exactly (same thin
:class:`~fcrm_common.http.ServiceClient` wrapper, same unverified mount
path caveat) rather than importing it: H1 and H2 are separate deployable
units and neither depends on the other's package, so the pattern is
duplicated by design, not by oversight. Every state transition this
package applies writes exactly one audit record through this client, in
the governed trail S4 owns (`run_events` is Caseflow's own replayable
ledger; S4 is the chain-hashed one a human sees).
"""

from __future__ import annotations

import logging
from typing import Any, Final, Optional
from urllib.parse import quote

from fcrm_common.http import ServiceClient
from fcrm_contracts.api.audit import (
    AuditRecord,
    Proposal,
    ProposalAppend,
    RecordAppend,
    VerifyResult,
)
from fcrm_contracts.common import Page

logger = logging.getLogger(__name__)

RECORDS_PATH: Final = "/records"
PROPOSALS_PATH: Final = "/proposals"

VERIFY_PATH: Final = "/verify/{case_id}"
RECORD_LIST_PATH: Final = "/records/{case_id}"

#: One page of a case's records and how many pages are read: a case's chain is
#: about seventy records, so this reads up to a thousand and no more.
RECORDS_PAGE_LIMIT: Final = 200
RECORDS_MAX_PAGES: Final = 5


class AuditClient:
    """Appends records to one case's hash-chained audit trail via S4."""

    def __init__(self, client: ServiceClient) -> None:
        """Wrap a :class:`ServiceClient` already pointed at ``AUDIT_SERVICE_URL``."""
        self._client = client

    async def append(
        self, record: RecordAppend, *, idempotency_key: Optional[str] = None
    ) -> AuditRecord:
        """POST ``record``; S4 computes ``seq``/``prev_hash``/``hash``.

        Args:
            record: What to append.
            idempotency_key: Sent as the ``Idempotency-Key`` header. A harness
                step derives one per record (:meth:`fcrm_caseflow.steps
                .StepDeps.append_record`) so that rerunning the step cannot
                chain the same record twice, once S4 honours the header.

        Raises:
            fcrm_common.errors.FcrmError: a subclass matching S4's error
                response (see :func:`fcrm_common.http.raise_for_problem`).
        """
        body = await self._client.request_json(
            "POST",
            RECORDS_PATH,
            json=record.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        logger.debug(
            "audit record appended",
            extra={"case_id": record.case_id, "type": record.type},
        )
        return AuditRecord.model_validate(body)

    async def append_proposal(
        self, proposal: ProposalAppend, *, idempotency_key: Optional[str] = None
    ) -> Proposal:
        """POST ``proposal`` to S4's evidence ledger (``POST /proposals``).

        The ledger is append-only: a later version of a proposal is a new entry
        whose ``supersedes`` names the one it replaces. With an
        ``idempotency_key`` a repeat returns the entry already stored, so a
        harness step that runs twice writes each proposal once.

        Raises:
            fcrm_common.errors.ValidationError: ``supersedes`` names no proposal
                of this case.
            fcrm_common.errors.ConflictError: ``supersedes`` already has a
                successor, or the key was used for a different proposal.
        """
        body = await self._client.request_json(
            "POST",
            PROPOSALS_PATH,
            json=proposal.model_dump(mode="json"),
            idempotency_key=idempotency_key,
        )
        logger.debug(
            "proposal appended",
            extra={
                "case_id": proposal.case_id,
                "agent": proposal.agent,
                "kind": proposal.kind,
            },
        )
        return Proposal.model_validate(body)

    async def verify(self, case_id: str) -> VerifyResult:
        """``GET /verify/{case_id}``: S4 recomputes the case's whole chain and
        says whether it is intact (and the first broken ``seq`` when it is not).

        Raises:
            fcrm_common.errors.FcrmError: S4 refused or was unreachable.
        """
        body = await self._client.request_json(
            "GET", VERIFY_PATH.format(case_id=quote(case_id, safe=""))
        )
        return VerifyResult.model_validate(body)

    async def list_records(self, case_id: str) -> list[AuditRecord]:
        """``GET /records/{case_id}``: the case's chain, oldest first, at most
        :data:`RECORDS_PAGE_LIMIT` times :data:`RECORDS_MAX_PAGES` records (the
        harness monitor's log).

        Raises:
            fcrm_common.errors.FcrmError: S4 refused or was unreachable.
        """
        path = RECORD_LIST_PATH.format(case_id=quote(case_id, safe=""))
        found: list[AuditRecord] = []
        cursor: Optional[str] = None
        for _ in range(RECORDS_MAX_PAGES):
            params: dict[str, Any] = {"limit": RECORDS_PAGE_LIMIT}
            if cursor is not None:
                params["cursor"] = cursor
            body = await self._client.request_json("GET", path, params=params)
            page = Page[AuditRecord].model_validate(body)
            found.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                break
        return found
