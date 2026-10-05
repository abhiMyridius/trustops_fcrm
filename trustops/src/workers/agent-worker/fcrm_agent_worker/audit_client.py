"""The S4 audit-service client (design A §2.4: ``POST /records``).

H2 never writes an audit record to Mongo itself — S4 is the only process
holding the insert-only ``fcrm_audit_writer`` credential and the only place
a chain hash is computed (``fcrm_audit.chain``, cloned nowhere). This module
is a thin :class:`~fcrm_common.http.ServiceClient` wrapper, the same pattern
``fcrm_common.http`` documents for any service-to-service call.

S4 does not yet have a FastAPI app in this repository (only
``fcrm_audit.chain`` and ``fcrm_audit.seedmodel`` exist; see that package's
``pyproject.toml``), so the exact mount path is unverified here — design A
§2.4's route table lists the service's own route as bare ``POST /records``,
while §1.4's "Conventions for every service" mounts S2-S5 under
``/api/fcrm/<service>``. :data:`RECORDS_PATH` is therefore a constant,
not a guess baked into call sites, so the one line to change when S4 lands
is obvious.
"""

from __future__ import annotations

import logging
from typing import Final

from fcrm_common.http import ServiceClient
from fcrm_contracts.api.audit import AuditRecord, RecordAppend

logger = logging.getLogger(__name__)

RECORDS_PATH: Final = "/records"


class AuditClient:
    """Appends records to one case's hash-chained audit trail via S4."""

    def __init__(self, client: ServiceClient) -> None:
        """Wrap a :class:`ServiceClient` already pointed at ``AUDIT_SERVICE_URL``."""
        self._client = client

    async def append(self, record: RecordAppend) -> AuditRecord:
        """POST ``record``; S4 computes ``seq``/``prev_hash``/``hash``.

        Raises:
            fcrm_common.errors.FcrmError: a subclass matching S4's error
                response (see :func:`fcrm_common.http.raise_for_problem`).
        """
        body = await self._client.request_json(
            "POST", RECORDS_PATH, json=record.model_dump(mode="json")
        )
        logger.debug(
            "audit record appended",
            extra={"case_id": record.case_id, "type": record.type},
        )
        return AuditRecord.model_validate(body)
