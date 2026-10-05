"""Index Contract metadata validation (design A §6.4's "Index Contract
metadata keys to add"). Every S3 Vectors upsert and every search filter
is checked against this allow-list, so a typo'd key fails loudly at write
or query time rather than silently matching nothing.
"""

from __future__ import annotations

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
