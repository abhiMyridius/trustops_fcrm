"""The S5 decision-service client: the two calls the assessment half makes,
building the frozen pack and superseding it.

Kept thin on purpose. ``POST /packs`` is idempotent by content hash (the same
snapshot returns the pack it already built; a changed one mints the next
version and supersedes the old), which is what makes the lock step safe to
repeat. Everything about votes, release and outcomes belongs to the decision
core, not here.

``supersede`` is coded against the route the S5 lane adds
(``POST /packs/{pack_id}/supersede``); its answer is not parsed, only its status
is checked, so a reshaped response cannot break the unlock step.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final, Optional

from fcrm_common.http import ServiceClient, Timeouts
from fcrm_contracts.api.decision import Pack, PackBuild

from fcrm_caseflow.clients import parse_body, path_segment

logger = logging.getLogger(__name__)

DEPENDENCY: Final = "decision"
PACKS_PATH: Final = "/packs"
SUPERSEDE_PATH: Final = "/packs/{pack_id}/supersede"

#: ``/packs`` read timeout 30 s (design A §10.3): the snapshot is large and S5
#: hashes it.
PACK_TIMEOUTS: Final = Timeouts(connect=2.0, read=30.0, write=30.0, pool=5.0)


class DecisionClient:
    """Calls S5 on behalf of H1's steps.

    Args:
        client: A :class:`ServiceClient` already pointed at
            ``DECISION_SERVICE_URL``.
    """

    def __init__(self, client: ServiceClient) -> None:
        """Wrap ``client``; it is shared with nothing else."""
        self._client = client

    async def build_pack(
        self,
        case_id: str,
        snapshot: Mapping[str, Any],
        *,
        idempotency_key: Optional[str] = None,
    ) -> Pack:
        """``POST /packs``: freeze ``snapshot`` as the case's pack (or get the
        current one back when nothing changed).

        Raises:
            fcrm_common.errors.ConflictError: the case already has a committee
                outcome, so no new pack may be built.
            fcrm_common.errors.UpstreamTimeoutError: no answer within 30 s.
        """
        build = PackBuild(case_id=case_id, snapshot=dict(snapshot))
        body = await self._client.request_json(
            "POST",
            PACKS_PATH,
            json=build.model_dump(mode="json"),
            idempotency_key=idempotency_key,
            timeout=PACK_TIMEOUTS,
        )
        return parse_body(Pack, body, dependency=DEPENDENCY, route=PACKS_PATH)

    async def supersede(
        self,
        pack_id: str,
        reason: str,
        *,
        idempotency_key: Optional[str] = None,
    ) -> None:
        """``POST /packs/{pack_id}/supersede``: the pack no longer stands (the
        recommendation was unlocked); votes cast on it are voided, kept.

        Raises:
            fcrm_common.errors.NotFoundError: no such pack.
            fcrm_common.errors.ConflictError: the pack is already decided.
        """
        await self._client.request_json(
            "POST",
            SUPERSEDE_PATH.format(pack_id=path_segment(pack_id)),
            json={"reason": reason},
            idempotency_key=idempotency_key,
            timeout=PACK_TIMEOUTS,
        )
