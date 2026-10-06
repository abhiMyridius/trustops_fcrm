"""What a failed job was given, read for the handoff gate (flow F-path).

H2 keeps the inputs of a job that failed after its retry
(:mod:`fcrm_contracts.events.raw_inputs`, ``agent_raw_inputs`` in the harness
database) and names them in ``agent.failed``. When H1 opens the ``handoff`` gate it
reads them here and puts a bounded rendering, a digest and the reference on the
gate's subject, so S1 can show the analyst what the agent saw and the audit record
can say which inputs the person worked from.

The reader never fails the handoff: inputs that are missing (the standalone H1 has
its own database, H2 could not save them) give a subject that names the reference
and says they are not available.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Mapping
from typing import Any, Optional, Protocol

from fcrm_common.mongo import Document, from_bson
from fcrm_contracts.events.raw_inputs import (
    GATE_INPUTS_MAX_CHARS,
    RAW_INPUTS_COLLECTION,
    AgentRawInputs,
)
from pydantic import ValidationError
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

logger = logging.getLogger(__name__)

_PREFIX = f"{RAW_INPUTS_COLLECTION}/"


class RawInputsReader(Protocol):
    """Where a failed job's inputs are read from."""

    async def read(
        self, ref: str, *, session: Optional[AsyncClientSession] = None
    ) -> Optional[AgentRawInputs]:
        """The inputs ``ref`` names, or ``None`` when there are none (or ``ref`` is
        not one of ours).
        """
        ...


class MongoRawInputsReader:
    """:class:`RawInputsReader` over ``agent_raw_inputs``.

    Args:
        collection: The collection (``_id`` is the job id).
    """

    def __init__(self, collection: AsyncCollection[Document]) -> None:
        self._collection = collection

    async def read(
        self, ref: str, *, session: Optional[AsyncClientSession] = None
    ) -> Optional[AgentRawInputs]:
        if not ref.startswith(_PREFIX):
            return None
        doc = await self._collection.find_one(
            {"_id": ref.removeprefix(_PREFIX)}, session=session
        )
        if doc is None:
            return None
        data = from_bson(dict(doc))
        data["job_id"] = data.pop("_id")
        try:
            return AgentRawInputs.model_validate(data)
        except ValidationError:
            logger.exception(
                "stored raw inputs do not have their shape", extra={"ref": ref}
            )
            return None


def inputs_fields(
    inputs: Optional[AgentRawInputs], *, limit: int = GATE_INPUTS_MAX_CHARS
) -> dict[str, str]:
    """The gate-subject fields that carry ``inputs``: the text (cut to ``limit``),
    its SHA-256 and size in bytes (of the stored text, before any cut), whether
    either cut applied, and the pack hash. Empty when there are no inputs.
    """
    if inputs is None:
        return {}
    encoded = inputs.text.encode("utf-8")
    fields = {
        "raw_inputs": inputs.text[:limit],
        "raw_inputs_sha256": hashlib.sha256(encoded).hexdigest(),
        "raw_inputs_bytes": str(len(encoded)),
        "raw_inputs_truncated": str(
            inputs.truncated or len(inputs.text) > limit
        ).lower(),
    }
    if inputs.pack_hash:
        fields["raw_inputs_pack_hash"] = inputs.pack_hash
    if inputs.error:
        fields["raw_inputs_error"] = inputs.error
    return fields


def digest_of(subject: Mapping[str, Any]) -> dict[str, Any]:
    """What an audit record says about the inputs a handoff showed: the reference,
    digest, size and whether the text was cut; never the text itself.
    """
    keys = (
        "raw_inputs_ref",
        "raw_inputs_sha256",
        "raw_inputs_bytes",
        "raw_inputs_truncated",
        "raw_inputs_pack_hash",
        "raw_inputs_error",
    )
    return {key: subject[key] for key in keys if key in subject}
