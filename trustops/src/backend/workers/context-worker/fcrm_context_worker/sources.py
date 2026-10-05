"""Where H4 reads what it embeds: S3's internal routes, as the service account.

H4 owns the vectors and S3 owns the data (design A §2.3, §6.3), so a build
starts by reading the rows back over HTTP:

* ``GET /internal/clauses`` (paged) for ``policy_clauses``;
* ``GET /internal/precedents`` (paged) for ``precedent_cases``;
* ``GET /controls?version=`` for ``controls`` (the inventory is one document).

Reads are *bounded*: at most :data:`MAX_PAGES` pages of :data:`PAGE_SIZE`
(2,000 rows), then the build refuses rather than embedding a truncated corpus.
Every call has the client's timeouts (2 s connect, 10 s read) and its retries
for an idempotent GET, and a peer failure maps onto ``fcrm_common.errors``: a
missing version is :class:`RecordsNotFoundError` (an answer, ``index.failed``),
everything else propagates for the consumer to retry.
"""

from __future__ import annotations

import logging
from typing import Any, Final, Optional, Protocol, TypeVar

from fcrm_common.errors import NotFoundError, UpstreamUnavailableError
from fcrm_common.http import ServiceClient
from fcrm_contracts.api.policy import (
    ClauseDetailOut,
    ControlInventoryOut,
    ControlOut,
    PrecedentIndexRecord,
)
from fcrm_contracts.common import Page
from pydantic import BaseModel
from pydantic import ValidationError as PydanticValidationError

logger = logging.getLogger(__name__)

PAGE_SIZE: Final = 200
"""Rows asked of a paged route at once (S3's maximum)."""
MAX_PAGES: Final = 10
"""Pages read at most: ``PAGE_SIZE * MAX_PAGES`` rows."""
POLICY_PEER: Final = "policy"

M = TypeVar("M", bound=BaseModel)


class RecordsNotFoundError(LookupError):
    """S3 has nothing to build the index from: no such corpus or inventory
    version, or no rows at all. An answer about the request, not an outage.
    """


class RecordLimitError(RuntimeError):
    """S3 holds more rows than one build reads; embedding part of an index would
    leave it quietly incomplete, so the build refuses.
    """


class RecordSource(Protocol):
    """What the builder reads its rows from."""

    async def clauses(self, version: int) -> list[ClauseDetailOut]:
        """Every clause of corpus ``version``."""
        ...

    async def precedents(self) -> list[PrecedentIndexRecord]:
        """Every stored precedent."""
        ...

    async def controls(self, version: int) -> list[ControlOut]:
        """Every control of inventory ``version``."""
        ...


class PolicyRecordSource:
    """:class:`RecordSource` over S3's routes.

    Args:
        policy: The service-account client for S3 (the caller owns and closes
            it).
    """

    def __init__(self, policy: ServiceClient) -> None:
        self._policy = policy

    async def _get(
        self, path: str, model: type[M], params: Optional[dict[str, Any]] = None
    ) -> M:
        """One GET, validated as ``model``.

        Raises:
            RecordsNotFoundError: S3 answered 404.
            UpstreamUnavailableError: S3 answered something that is not
                ``model`` (or could not be reached).
        """
        try:
            payload = await self._policy.request_json("GET", path, params=params)
        except NotFoundError as exc:
            raise RecordsNotFoundError(
                f"S3 has nothing at {path} {params or ''}"
            ) from exc
        try:
            return model.model_validate(payload)
        except PydanticValidationError as exc:
            logger.error(
                "S3 answered with an unexpected shape",
                extra={"path": path, "errors": exc.error_count()},
            )
            raise UpstreamUnavailableError(
                f"policy returned an invalid {model.__name__}.",
                dependency=POLICY_PEER,
            ) from exc

    async def _paged(
        self, path: str, page_model: type[Page[M]], params: dict[str, Any]
    ) -> list[M]:
        """Every row of a cursor-paged route, up to the bound.

        Raises:
            RecordLimitError: There are more than ``PAGE_SIZE * MAX_PAGES`` rows.
        """
        rows: list[M] = []
        cursor: Optional[str] = None
        for _ in range(MAX_PAGES):
            query = {**params, "limit": PAGE_SIZE}
            if cursor is not None:
                query["cursor"] = cursor
            page = await self._get(path, page_model, query)
            rows.extend(page.items)
            cursor = page.next_cursor
            if cursor is None:
                return rows
        raise RecordLimitError(
            f"{path} holds more than {PAGE_SIZE * MAX_PAGES} rows; refusing a "
            "partial index"
        )

    async def clauses(self, version: int) -> list[ClauseDetailOut]:
        """Every clause of corpus ``version``, in id order.

        Raises:
            RecordsNotFoundError: S3 holds no such corpus version.
        """
        return await self._paged(
            "/internal/clauses", Page[ClauseDetailOut], {"corpus_version": version}
        )

    async def precedents(self) -> list[PrecedentIndexRecord]:
        """Every stored precedent, in case-id order."""
        return await self._paged("/internal/precedents", Page[PrecedentIndexRecord], {})

    async def controls(self, version: int) -> list[ControlOut]:
        """Every control of inventory ``version``.

        Raises:
            RecordsNotFoundError: S3 holds no such inventory version.
        """
        inventory = await self._get(
            "/controls", ControlInventoryOut, {"version": f"v{version}"}
        )
        return inventory.controls
