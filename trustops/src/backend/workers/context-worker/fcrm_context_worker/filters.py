"""Search-filter allow-list for H4, and its translation to a vector-store filter.

The first three symbols are a deliberate, verbatim duplicate of
``fcrm_policy.search.filters`` (S3 policy-service). H4 is a worker and S3 a
service; neither depends on the other at runtime — the same precedent
``fcrm_decision`` set by duplicating its hashing helper rather than
importing across services. A unit test
(``tests/unit/workers/context-worker/fcrm_context_worker/test_filters.py``)
imports both copies and fails on any drift. Both copies should move to a
shared package (``fcrm_contracts``) in a follow-up.

:func:`to_vector_filter` is H4's own addition: it validates caller filters
with :func:`validate_metadata` and emits the equality filter the cloned
engine's ``search(filters=...)`` pushes down to the backend (S3 Vectors
server-side, or the ``memory`` backend's emulation).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Optional

logger = logging.getLogger(__name__)

# --- verbatim copy of fcrm_policy.search.filters (keep identical) ----------

#: The base keys evoq_context's Index Contract v2 already defines, plus
#: the five this project adds (design A §6.4): corpus_version,
#: trigger_type, risk_area, jurisdiction, closed_date. ``jurisdiction``
#: already existed upstream; the other four are new here.
ALLOWED_METADATA_KEYS: frozenset[str] = frozenset(
    {
        "source_id",
        "jurisdiction",
        "corpus_version",
        "trigger_type",
        "risk_area",
        "closed_date",
    }
)


def validate_metadata(metadata: dict[str, object]) -> list[str]:
    """Return every key in ``metadata`` that is not in
    :data:`ALLOWED_METADATA_KEYS`; an empty list means the metadata is
    valid to upsert or filter on.
    """
    return [key for key in metadata if key not in ALLOWED_METADATA_KEYS]


def matches_filters(metadata: dict[str, object], filters: dict[str, object]) -> bool:
    """Used by the ``memory`` VECTOR_BACKEND (ADR-12) to emulate S3
    Vectors' server-side metadata filtering locally: every non-None
    filter value must equal the stored metadata's value for that key.
    A filter key not in ``ALLOWED_METADATA_KEYS`` is a caller bug, not a
    silent non-match — validate with :func:`validate_metadata` first.
    """
    return all(
        metadata.get(key) == value
        for key, value in filters.items()
        if value is not None
    )


# --- H4 additions -----------------------------------------------------------


class UnknownFilterKeyError(ValueError):
    """A search filter names a key outside :data:`ALLOWED_METADATA_KEYS`."""

    def __init__(self, keys: list[str]) -> None:
        """Record the offending keys."""
        super().__init__(
            f"unknown search filter key(s) {sorted(keys)}; "
            f"allowed: {sorted(ALLOWED_METADATA_KEYS)}"
        )
        self.keys = keys


def to_vector_filter(
    filters: Optional[Mapping[str, Optional[str]]],
) -> Optional[dict[str, object]]:
    """Validate ``filters`` and return the engine filter (``None`` if empty).

    Unknown keys raise :class:`UnknownFilterKeyError` — rejected, never
    silently ignored. ``None`` values mean "no constraint on this key" and
    are dropped, exactly as :func:`matches_filters` treats them.
    """
    if not filters:
        return None
    unknown = validate_metadata(dict(filters))
    if unknown:
        logger.warning("rejected search filter keys %s", unknown)
        raise UnknownFilterKeyError(unknown)
    active: dict[str, object] = {k: v for k, v in filters.items() if v is not None}
    return active or None
