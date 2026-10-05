"""The HTTP tools that read S1 for more than a case's record:
``evidence_store`` (S3's control catalogue and S1's evidence), ``submission_diff``
(two submission versions of a case) and ``case_search`` (cases like a query).

**Which S1 routes, and how sure we are of them**

* ``submission_diff`` reads ``GET /cases/{id}/versions/diff?from_version=&to_version=``,
  which S1 already serves in the tool's own output shape
  (:class:`~fcrm_contracts.tools.submission_diff.SubmissionDiffOutput`).
* ``evidence_store`` reads ``GET /internal/cases/{id}/evidence?control_id=`` and
  ``case_search`` reads ``GET /internal/cases/search?q=``. Neither route exists
  yet (the assessment lane adds them), so both are coded against an *assumed*
  shape: :class:`~fcrm_contracts.api.case_evidence.CaseEvidenceOut` for the
  first and the tool's own :class:`~fcrm_contracts.tools.case_search.CaseSearchOutput`
  for the second, each read tolerantly and tested with ``httpx.MockTransport``.
  When S1's route lands with another shape, this module is the one place that
  changes.

Nothing here falls back to seed data: a peer that cannot answer is an error the
model sees as a tool failure.
"""

from __future__ import annotations

import logging
from typing import Any, Final

from fcrm_common.cache import TTLCache, cache_get, cache_set
from fcrm_common.http import ServiceClient
from fcrm_contracts.api.case_evidence import CaseEvidenceItem, CaseEvidenceOut
from fcrm_contracts.api.policy import ControlInventoryOut, ControlOut
from fcrm_contracts.tools.case_search import CaseSearchInput, CaseSearchOutput
from fcrm_contracts.tools.evidence_store import (
    EvidenceItem,
    EvidenceStoreInput,
    EvidenceStoreOutput,
)
from fcrm_contracts.tools.submission_diff import (
    SubmissionDiffInput,
    SubmissionDiffOutput,
)

from fcrm_tools.base import BaseTool
from fcrm_tools.errors import ToolInputError, ToolScopeError, UnknownEntityError
from fcrm_tools.http_common import (
    ALIAS_TTL_S,
    check_top_k,
    fetch_model,
    segment,
)

logger = logging.getLogger(__name__)

MAX_QUERY_CHARS: Final = 1000
"""Longest ``case_search`` query sent in a URL (a longer one is refused)."""
_ACTIVE_CONTROLS_KEY: Final = "evidence_store:active_controls"


class HttpEvidenceStoreTool(BaseTool[EvidenceStoreInput, EvidenceStoreOutput]):
    """``evidence_store``: what evidence exists for one control on one case.

    S1 holds the case's evidence; S3's control catalogue says whether the
    control exists and when it was last tested, which is the date reported for
    an item S1 gives no date for. The staleness *verdict* is not made here
    (date arithmetic in code, elsewhere): this returns the raw date and the
    artefact's description, and an empty list when the case has none.
    """

    name = "evidence_store"
    input_model = EvidenceStoreInput
    output_model = EvidenceStoreOutput

    def __init__(
        self, case: ServiceClient, policy: ServiceClient, cache: TTLCache
    ) -> None:
        """Read evidence from S1 (``case``) and the catalogue from S3 (``policy``)."""
        self._case = case
        self._policy = policy
        self._cache = cache

    async def _control(self, control_id: str) -> ControlOut:
        """The catalogue entry of ``control_id`` in the active inventory.

        Raises:
            UnknownEntityError: the inventory has no such control.
        """
        cached = await cache_get(self._cache, _ACTIVE_CONTROLS_KEY)
        if cached is not None:
            inventory = ControlInventoryOut.model_validate_json(cached)
        else:
            inventory = await fetch_model(
                self._policy,
                "/controls",
                ControlInventoryOut,
                tool=self.name,
                what="the active control inventory",
            )
            await cache_set(
                self._cache,
                _ACTIVE_CONTROLS_KEY,
                inventory.model_dump_json(),
                ALIAS_TTL_S,
            )
        found = next(
            (c for c in inventory.controls if c.control_id == control_id), None
        )
        if found is None:
            msg = f"no control {control_id!r} in the inventory"
            raise UnknownEntityError(msg)
        return found

    @staticmethod
    def _item(item: CaseEvidenceItem, control: ControlOut) -> EvidenceItem:
        """One S1 evidence item as the tool reports it: the artefact's
        description, its own date or else the control's last test date, and the
        document it came from when it is one.
        """
        doc_id = item.doc_id
        if doc_id is None and item.source_ref is not None:
            ref_doc = item.source_ref.get("doc_id")
            doc_id = ref_doc if isinstance(ref_doc, str) else None
        return EvidenceItem(
            artifact=item.summary,
            evidence_date=item.evidence_date or control.last_tested or "",
            doc_id=doc_id,
        )

    async def run(self, request: EvidenceStoreInput) -> EvidenceStoreOutput:
        """The evidence items of ``request.control_id`` on ``request.case_id``.

        Raises:
            UnknownEntityError: no such control, or S1 has no such case.
            ToolScopeError: S1 answered for another control.
        """
        control = await self._control(request.control_id)
        evidence = await fetch_model(
            self._case,
            f"/internal/cases/{segment(request.case_id)}/evidence",
            CaseEvidenceOut,
            tool=self.name,
            what=f"case {request.case_id!r}",
            params={"control_id": request.control_id},
        )
        if evidence.control_id != request.control_id:
            msg = (
                f"evidence of {request.control_id} requested; "
                f"S1 answered {evidence.control_id}"
            )
            raise ToolScopeError(msg)
        return EvidenceStoreOutput(
            control_id=request.control_id,
            items=[self._item(item, control) for item in evidence.items],
        )


class HttpSubmissionDiffTool(BaseTool[SubmissionDiffInput, SubmissionDiffOutput]):
    """``submission_diff``: which fields changed between two submission versions
    of a case, so a ``changed_fields`` re-run re-proposes only those."""

    name = "submission_diff"
    input_model = SubmissionDiffInput
    output_model = SubmissionDiffOutput

    def __init__(self, case: ServiceClient) -> None:
        """Read the diff from S1 through ``case``."""
        self._case = case

    async def run(self, request: SubmissionDiffInput) -> SubmissionDiffOutput:
        """The fields whose value differs between the two versions.

        Raises:
            ToolInputError: a version below 1.
            UnknownEntityError: S1 has no such case or version.
        """
        for version in (request.from_version, request.to_version):
            if version < 1:
                msg = f"submission versions start at 1, got {version}"
                raise ToolInputError(msg)
        return await fetch_model(
            self._case,
            f"/cases/{segment(request.case_id)}/versions/diff",
            SubmissionDiffOutput,
            tool=self.name,
            what=(
                f"case {request.case_id!r} submission versions "
                f"{request.from_version} and {request.to_version}"
            ),
            params={
                "from_version": request.from_version,
                "to_version": request.to_version,
            },
        )


class HttpCaseSearchTool(BaseTool[CaseSearchInput, CaseSearchOutput]):
    """``case_search``: cases like a free-text query, optionally narrowed to
    change types and a geography, closed ones only by default."""

    name = "case_search"
    input_model = CaseSearchInput
    output_model = CaseSearchOutput

    def __init__(self, case: ServiceClient) -> None:
        """Search through S1 (``case``)."""
        self._case = case

    async def run(self, request: CaseSearchInput) -> CaseSearchOutput:
        """Top cases for ``request.query``, best match first.

        Raises:
            ToolInputError: an empty or over-long query, or ``top_k`` out of
                range.
        """
        check_top_k(request.top_k)
        query = request.query.strip()
        if not query or len(query) > MAX_QUERY_CHARS:
            msg = f"query must be 1..{MAX_QUERY_CHARS} characters"
            raise ToolInputError(msg)
        params: dict[str, Any] = {
            "q": query,
            "top_k": request.top_k,
            "closed_only": "true" if request.closed_only else "false",
        }
        if request.change_types:
            params["change_types"] = ",".join(request.change_types)
        if request.geography:
            params["geography"] = request.geography
        found = await fetch_model(
            self._case,
            "/internal/cases/search",
            CaseSearchOutput,
            tool=self.name,
            what=f"case search {query[:40]!r}",
            params=params,
        )
        if len(found.hits) > request.top_k:
            logger.warning(
                "S1 returned more cases than asked for; trimming",
                extra={"tool": self.name, "asked": request.top_k},
            )
            return CaseSearchOutput(hits=found.hits[: request.top_k])
        return found


__all__ = [
    "MAX_QUERY_CHARS",
    "HttpCaseSearchTool",
    "HttpEvidenceStoreTool",
    "HttpSubmissionDiffTool",
]
