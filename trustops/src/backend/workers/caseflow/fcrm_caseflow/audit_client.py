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
from typing import Final, Optional

from fcrm_common.http import ServiceClient
from fcrm_contracts.api.audit import AuditRecord, RecordAppend

logger = logging.getLogger(__name__)

RECORDS_PATH: Final = "/records"


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
