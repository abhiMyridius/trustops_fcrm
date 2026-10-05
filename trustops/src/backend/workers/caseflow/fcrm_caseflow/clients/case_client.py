"""The S1 case-service client: the internal routes only H1 calls
(``/internal/...``, service-account bearer, role ``fcrm_service``).

One method per route, typed with :mod:`fcrm_contracts.api.case_internal`.
Every write is **idempotent** by construction (PUT and PATCH carry the whole
intended state), because a harness step may run again after a failure; the
two POSTs (``assign`` and ``events``) are not idempotent by nature, so they
accept an ``idempotency_key`` that goes out as the ``Idempotency-Key`` header
(and lets :class:`~fcrm_common.http.ServiceClient` retry them on a 5xx).
"""

from __future__ import annotations

import logging
from typing import Any, Final, Optional

from fcrm_common.errors import UpstreamUnavailableError
from fcrm_common.http import ServiceClient
from fcrm_contracts.api.case_internal import (
    AnalystLoadResponse,
    AssignAnalystRequest,
    CaseRecordInternal,
    DocumentInternal,
    DocumentPatch,
    IntakePush,
    ItemsUpsert,
    ItemsUpsertResult,
    ReviewItemUpsert,
    StatePatch,
    UiEventIn,
)
from fcrm_contracts.api.case_internal_outcome import OutcomePut, OutcomePutResult
from fcrm_contracts.enums import ItemKind

from fcrm_caseflow.clients import CALL_TIMEOUTS, parse_body, path_segment

logger = logging.getLogger(__name__)

DEPENDENCY: Final = "case"

#: Route templates, relative to ``CASE_SERVICE_URL`` (which already ends in S1's
#: mount prefix, ``/api/fcrm``).
RECORD_PATH: Final = "/internal/cases/{case_id}/record"
ITEMS_PATH: Final = "/internal/cases/{case_id}/items"
INTAKE_PATH: Final = "/internal/cases/{case_id}/intake"
STATE_PATH: Final = "/internal/cases/{case_id}/state"
ASSIGN_PATH: Final = "/internal/cases/{case_id}/assign"
EVENTS_PATH: Final = "/internal/cases/{case_id}/events"
OUTCOME_PATH: Final = "/internal/cases/{case_id}/outcome"
DOCUMENT_PATH: Final = "/internal/documents/{doc_id}"
ANALYST_LOAD_PATH: Final = "/internal/analysts/load"


def _fill(template: str, **values: str) -> str:
    """``template`` with each value made safe as a path segment."""
    return template.format(**{k: path_segment(v) for k, v in values.items()})


def _json(model: Any) -> Any:
    """``model`` as the JSON-safe body the peer receives."""
    return model.model_dump(mode="json")


class CaseClient:
    """Calls S1's internal routes on behalf of H1's steps.

    Args:
        client: A :class:`ServiceClient` already pointed at
            ``CASE_SERVICE_URL``.
    """

    def __init__(self, client: ServiceClient) -> None:
        """Wrap ``client``; it is shared with nothing else."""
        self._client = client

    async def get_record(self, case_id: str) -> CaseRecordInternal:
        """``GET /internal/cases/{case_id}/record``: the case as H1 reads it.

        Raises:
            fcrm_common.errors.NotFoundError: no such case.
            fcrm_common.errors.FcrmError: S1 refused or was unreachable.
        """
        route = _fill(RECORD_PATH, case_id=case_id)
        body = await self._client.request_json("GET", route, timeout=CALL_TIMEOUTS)
        return parse_body(
            CaseRecordInternal, body, dependency=DEPENDENCY, route=RECORD_PATH
        )

    async def get_document(self, doc_id: str) -> DocumentInternal:
        """``GET /internal/documents/{doc_id}``.

        Raises:
            fcrm_common.errors.NotFoundError: no such document.
        """
        route = _fill(DOCUMENT_PATH, doc_id=doc_id)
        body = await self._client.request_json("GET", route, timeout=CALL_TIMEOUTS)
        return parse_body(
            DocumentInternal, body, dependency=DEPENDENCY, route=DOCUMENT_PATH
        )

    async def patch_document(self, doc_id: str, patch: DocumentPatch) -> None:
        """``PATCH /internal/documents/{doc_id}``: record what H3 produced.
        Only the fields that are set are sent. Idempotent: the patch carries
        the final status and keys.

        Raises:
            fcrm_common.errors.NotFoundError: no such document.
        """
        await self._client.request_json(
            "PATCH",
            _fill(DOCUMENT_PATH, doc_id=doc_id),
            json=patch.model_dump(mode="json", exclude_none=True),
            timeout=CALL_TIMEOUTS,
        )

    async def put_intake(self, case_id: str, push: IntakePush) -> None:
        """``PUT /internal/cases/{case_id}/intake``: everything the intake
        agents proposed, merged, in one idempotent write.
        """
        await self._client.request_json(
            "PUT",
            _fill(INTAKE_PATH, case_id=case_id),
            json=_json(push),
            timeout=CALL_TIMEOUTS,
        )

    async def put_items(self, case_id: str, upsert: ItemsUpsert) -> ItemsUpsertResult:
        """``PUT /internal/cases/{case_id}/items``: bulk upsert of review
        items with caller-chosen stable ids.
        """
        body = await self._client.request_json(
            "PUT",
            _fill(ITEMS_PATH, case_id=case_id),
            json=_json(upsert),
            timeout=CALL_TIMEOUTS,
        )
        return parse_body(
            ItemsUpsertResult, body, dependency=DEPENDENCY, route=ITEMS_PATH
        )

    async def get_items(
        self, case_id: str, kind: Optional[ItemKind] = None
    ) -> list[ReviewItemUpsert]:
        """``GET /internal/cases/{case_id}/items[?kind=]``.

        The contract names no read model for items, so each row is read as the
        write shape (:class:`ReviewItemUpsert`) and fields S1 adds beyond it
        (decision, history...) are dropped here, not rejected. The body is
        ``{"items": [...]}`` or a bare list.

        Raises:
            fcrm_common.errors.UpstreamUnavailableError: the body is neither.
        """
        params = {"kind": kind.value} if kind is not None else None
        body = await self._client.request_json(
            "GET",
            _fill(ITEMS_PATH, case_id=case_id),
            params=params,
            timeout=CALL_TIMEOUTS,
        )
        rows = body.get("items") if isinstance(body, dict) else body
        if not isinstance(rows, list):
            raise UpstreamUnavailableError(
                f"{DEPENDENCY} answered {ITEMS_PATH} with an unexpected body",
                dependency=DEPENDENCY,
            )
        known = set(ReviewItemUpsert.model_fields)
        return [
            parse_body(
                ReviewItemUpsert,
                {k: v for k, v in row.items() if k in known},
                dependency=DEPENDENCY,
                route=ITEMS_PATH,
            )
            for row in rows
        ]

    async def patch_state(self, case_id: str, patch: StatePatch) -> None:
        """``PATCH /internal/cases/{case_id}/state``: H1 decides every
        transition and tells S1 where the case now is. Only set fields change.
        """
        await self._client.request_json(
            "PATCH",
            _fill(STATE_PATH, case_id=case_id),
            json=patch.model_dump(mode="json", exclude_none=True),
            timeout=CALL_TIMEOUTS,
        )

    async def assign(
        self,
        case_id: str,
        request: AssignAnalystRequest,
        *,
        idempotency_key: Optional[str] = None,
    ) -> None:
        """``POST /internal/cases/{case_id}/assign``: R5's result."""
        await self._client.request_json(
            "POST",
            _fill(ASSIGN_PATH, case_id=case_id),
            json=_json(request),
            idempotency_key=idempotency_key,
            timeout=CALL_TIMEOUTS,
        )

    async def post_event(
        self,
        case_id: str,
        event: UiEventIn,
        *,
        idempotency_key: Optional[str] = None,
    ) -> None:
        """``POST /internal/cases/{case_id}/events``: show ``event`` on the case
        stream and in the activity feed, to whoever ``event.visibility`` allows.
        """
        await self._client.request_json(
            "POST",
            _fill(EVENTS_PATH, case_id=case_id),
            json=_json(event),
            idempotency_key=idempotency_key,
            timeout=CALL_TIMEOUTS,
        )

    async def put_outcome(
        self, case_id: str, body: OutcomePut, *, idempotency_key: str
    ) -> OutcomePutResult:
        """``PUT /internal/cases/{case_id}/outcome``: record the outcome S5
        decided, activate its conditions and close the case, in one S1
        transaction. The body is the whole intended state and the key binds it
        to one body, so a step that runs twice closes the case once.

        Raises:
            fcrm_common.errors.NotFoundError: no such case.
            fcrm_common.errors.ConflictError: the case already has a different
                outcome, or the key belongs to another request.
        """
        body_json = await self._client.request_json(
            "PUT",
            _fill(OUTCOME_PATH, case_id=case_id),
            json=_json(body),
            idempotency_key=idempotency_key,
            timeout=CALL_TIMEOUTS,
        )
        return parse_body(
            OutcomePutResult, body_json, dependency=DEPENDENCY, route=OUTCOME_PATH
        )

    async def analyst_load(self) -> AnalystLoadResponse:
        """``GET /internal/analysts/load``: open cases per analyst (R5's input)."""
        body = await self._client.request_json(
            "GET", ANALYST_LOAD_PATH, timeout=CALL_TIMEOUTS
        )
        return parse_body(
            AnalystLoadResponse, body, dependency=DEPENDENCY, route=ANALYST_LOAD_PATH
        )
