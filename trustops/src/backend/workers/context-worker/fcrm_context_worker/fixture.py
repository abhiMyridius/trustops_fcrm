"""The vector fixture H4 writes and S3 reads (design A §6.3).

Under ``VECTOR_BACKEND=memory`` the vectors live in one process's memory, so H4
hands them to S3 as a file: after a build it dumps its in-memory store with
``dump_fixture`` and stores the JSON in the vectors bucket at
``{index}/{version}.json`` (:func:`fixture_key`), announces the key in
``index.built``, and S3 loads it into its own store
(:func:`decode_fixture`). The file carries vectors and their metadata only;
the text S3 returns for a hit always comes from Mongo, never from here.

Two failures are told apart because they are handled differently: a fixture
that cannot be used (:class:`FixtureError`: wrong shape, wrong dimension, bad
metadata) is a permanent fault of that file and is never retried, whereas an
object store that cannot be reached is the caller's transient problem.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping
from typing import Any, Final, Protocol

from evoq_context.core.errors import MetadataContractViolation
from evoq_context.vector.contract import validate_metadata
from evoq_context.vector.interface import VectorChunk

logger = logging.getLogger(__name__)

FIXTURE_VERSION: Final = 1
FIXTURE_CONTENT_TYPE: Final = "application/json"
_INDEX_NAME: Final = re.compile(r"^[a-z][a-z_]{0,40}$")


class FixtureError(ValueError):
    """The fixture cannot be used: it is not valid JSON, has the wrong shape or
    dimension, or carries metadata outside the Index Contract.
    """


class FixtureSource(Protocol):
    """A store that can dump itself as a fixture (the ``memory`` backend)."""

    def dump_fixture(self) -> dict[str, Any]:
        """Every stored record as the shared fixture document."""
        ...


def fixture_key(index: str, version: int) -> str:
    """``{index}/{version}.json``: where the fixture of one index at one
    version lives in the vectors bucket.

    Raises:
        FixtureError: ``index`` is not a plain index name, or ``version`` is
            not positive (so a key can never climb out of its prefix).
    """
    if _INDEX_NAME.match(index) is None or version < 1:
        raise FixtureError(f"no fixture key for index {index!r} version {version}")
    return f"{index}/{version}.json"


def encode_fixture(store: FixtureSource) -> bytes:
    """The store's fixture as UTF-8 JSON, ready to put in the bucket."""
    return json.dumps(store.dump_fixture(), separators=(",", ":")).encode("utf-8")


def _chunk(entry: Any, dimension: int) -> VectorChunk:
    """One fixture vector as a chunk, checked.

    Raises:
        FixtureError: The entry is malformed, its vector has the wrong length
            or its metadata breaks the Index Contract.
    """
    if not isinstance(entry, Mapping):
        raise FixtureError("a fixture vector is not an object")
    key, vector, metadata = entry.get("key"), entry.get("vector"), entry.get("metadata")
    if not isinstance(key, str) or not key:
        raise FixtureError("a fixture vector has no key")
    if not isinstance(vector, list) or len(vector) != dimension:
        raise FixtureError(f"vector {key!r} is not {dimension}-dimensional")
    if not isinstance(metadata, dict):
        raise FixtureError(f"vector {key!r} has no metadata")
    try:
        validate_metadata(metadata)
    except MetadataContractViolation as exc:
        raise FixtureError(f"vector {key!r}: {exc}") from exc
    return VectorChunk(
        chunk_id=key,
        text=str(metadata.get("text", "")),
        embedding=[float(x) for x in vector],
        metadata=metadata,
    )


def decode_fixture(data: bytes, *, dimension: int) -> list[VectorChunk]:
    """The vectors of a fixture file, validated.

    Args:
        data: The file's bytes.
        dimension: The dimension the loading process embeds queries at; a
            fixture at another dimension would rank every document as
            maximally distant, so it is refused rather than loaded.

    Raises:
        FixtureError: The file cannot be used.
    """
    try:
        document = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FixtureError("the fixture is not valid JSON") from exc
    if not isinstance(document, dict) or document.get("fixture_version") != 1:
        raise FixtureError("the fixture is not a version 1 vector fixture")
    if document.get("dimension") != dimension:
        raise FixtureError(
            f"the fixture is {document.get('dimension')}-dimensional; "
            f"this process embeds at {dimension}"
        )
    entries = document.get("vectors")
    if not isinstance(entries, list):
        raise FixtureError("the fixture has no vectors list")
    return [_chunk(entry, dimension) for entry in entries]
