"""Keeping what an agent was given when its job fails (flow F-path).

A job that fails after its retry sends the run to ``handoff`` and a person finishes
the step by hand. To do that they must see what the agent saw, so H2 writes the
job's inputs before it reports ``agent.failed`` and names them in
``AgentFailed.raw_inputs_ref`` (:mod:`fcrm_contracts.events.raw_inputs`). H1 reads
them when it opens the gate.

Saving never blocks the failure report: a store that is down costs the analyst the
inputs (the reference is left out and the log says why), not the handoff.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Protocol

from fcrm_agents.context_pack import ContextPack
from fcrm_common.mongo import Document, to_bson
from fcrm_common.time import utcnow
from fcrm_contracts.events.payloads import JobPayload
from fcrm_contracts.events.raw_inputs import (
    RAW_INPUTS_MAX_CHARS,
    AgentRawInputs,
    raw_inputs_ref,
)
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import PyMongoError

logger = logging.getLogger(__name__)


class RawInputsSink(Protocol):
    """Where a failed job's inputs are kept."""

    async def save(
        self,
        job: JobPayload,
        pack: Optional[ContextPack],
        *,
        error: Optional[str] = None,
    ) -> Optional[str]:
        """Keep the inputs of ``job`` (``pack`` is ``None`` when none could be
        built, ``error`` says why) and return their reference, or ``None`` when
        they could not be kept.
        """
        ...


def raw_inputs_of(
    job: JobPayload, pack: Optional[ContextPack], error: Optional[str]
) -> AgentRawInputs:
    """The inputs of a failed job as the document H2 stores: the job as dispatched
    and, when a pack was built, its sections as the model received them (cut to
    :data:`~fcrm_contracts.events.raw_inputs.RAW_INPUTS_MAX_CHARS`).
    """
    text = pack.user_text() if pack is not None else ""
    return AgentRawInputs(
        job_id=job.job_id,
        case_id=job.case_id,
        run_id=job.run_id,
        agent=job.agent.value,
        job=job.model_dump(mode="json"),
        text=text[:RAW_INPUTS_MAX_CHARS],
        truncated=len(text) > RAW_INPUTS_MAX_CHARS,
        pack_hash=pack.pack_hash if pack is not None else None,
        sections=pack.section_names() if pack is not None else [],
        error=error,
        saved_at=utcnow(),
    )


class MongoRawInputs:
    """:class:`RawInputsSink` over the harness database's ``agent_raw_inputs``.

    Args:
        collection: The collection (``_id`` is the job id).
    """

    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def save(
        self,
        job: JobPayload,
        pack: Optional[ContextPack],
        *,
        error: Optional[str] = None,
    ) -> Optional[str]:
        """Keep the inputs (once per job: a redelivered failure keeps the first)."""
        fields: dict[str, Any] = to_bson(
            raw_inputs_of(job, pack, error).model_dump(
                mode="python", exclude={"job_id"}
            )
        )
        try:
            await self._collection.update_one(
                {"_id": job.job_id}, {"$setOnInsert": fields}, upsert=True
            )
        except PyMongoError:
            logger.exception(
                "raw inputs not kept; the handoff will show none",
                extra={"job_id": job.job_id, "agent": job.agent.value},
            )
            return None
        return raw_inputs_ref(job.job_id)
