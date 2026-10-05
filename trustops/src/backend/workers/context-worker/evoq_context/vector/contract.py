"""Index Contract v2 / v2.1 constants, validation and metadata shrinking.

Scope & functionality
---------------------
The single place that knows the exact shape of a vector's metadata under
the Index Contract (``AEO/contracts/index_contract_v2.md`` +
``index_contract_v2.schema.json``, owned by this engine): the filterable
and non-filterable key sets, the deterministic vector-key formula, the one
list-join convention, ``validate_metadata`` (v2 and the additive v2.1 keys
``parent_key``/``chunk_level``/``topic_id``), and the single
``shrink_metadata`` every backend calls before a ``put`` — deterministic
``SHRINK_ORDER`` then ``SECONDARY_SHRINK_ORDER`` caps, then ``text`` down to
``TEXT_FALLBACK_CHARS`` (never popped), then ``MetadataTooLarge``. A
violation is a producer bug, not a transient failure, so validation raises
instead of silently dropping data.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence

from evoq_context.core import limits
from evoq_context.core.errors import MetadataContractViolation, MetadataTooLarge
from evoq_context.core.models import JsonValue
from evoq_context.core.observer import ContextObserver, GuardrailEvent

__all__ = [
    "ALL_METADATA_KEYS",
    "CHUNK_LEVEL_CHILD",
    "CHUNK_LEVEL_PARENT",
    "CHUNK_STRATEGIES",
    "CHUNK_TYPES",
    "FILTERABLE_KEYS",
    "INDEX_CONTRACT_REVISION",
    "INDEX_CONTRACT_VERSION",
    "LIST_METADATA_KEYS",
    "MAX_LIST_ITEMS",
    "MAX_METADATA_BYTES",
    "MAX_META_TEXT_CHARS",
    "NON_FILTERABLE_KEYS",
    "REQUIRED_METADATA_KEYS",
    "SECONDARY_SHRINK_ORDER",
    "SHRINK_ORDER",
    "SOURCE_TYPES",
    "TEXT_FALLBACK_CHARS",
    "MetadataContractViolation",
    "MetadataTooLarge",
    "content_hash_of",
    "doc_id_for",
    "join_list_metadata",
    "metadata_size_bytes",
    "shrink_metadata",
    "split_list_metadata",
    "split_metadata",
    "validate_filter_keys",
    "validate_metadata",
    "vector_key",
]

#: Version stamped on every vector this engine writes (``metadata.index_contract``).
INDEX_CONTRACT_VERSION = limits.INDEX_CONTRACT_VERSION
#: Additive revision written into ``index_manifest.contract_revision``.
INDEX_CONTRACT_REVISION = limits.INDEX_CONTRACT_REVISION

#: The seven deterministic chunking strategies (segmenter names).
CHUNK_STRATEGIES: tuple[str, ...] = (
    "heading_sections",
    "paragraph_windows",
    "page_windows",
    "slide_units",
    "table_aware",
    "requirement_units",
    "comment_thread",
    # TrustOps (design A §6.4): one vector per whole policy clause.
    "clause_units",
)

#: ``metadata.type`` values (source shape, not strategy).
CHUNK_TYPES: tuple[str, ...] = (
    "markdown",
    "plain_text",
    "pdf",
    "paged",
    "tabular",
    "slides",
    "jira",
    "confluence",
)

#: TrustOps adds ``policy`` (corpus clauses seeded/indexed by H4, design A §6.4)
#: and the other two indexes' record kinds: ``precedent`` (a closed case) and
#: ``control`` (a control-inventory entry).
SOURCE_TYPES: tuple[str, ...] = (
    "confluence",
    "jira",
    "file",
    "policy",
    "precedent",
    "control",
)

#: ``chunk_level`` values (v2.1): parents are stored-only, children are embedded.
CHUNK_LEVEL_PARENT = 0
CHUNK_LEVEL_CHILD = 1

#: Filterable metadata keys (declared at create_index). v2.1 adds
#: ``parent_key``, ``chunk_level``, ``topic_id`` — all optional.
FILTERABLE_KEYS: frozenset[str] = frozenset(
    {
        "doc_id",
        "project",
        "source_type",
        "source_id",
        "content_hash",
        "chunk_key",
        "chunk_index",
        "offset",
        "chunk_strategy",
        "type",
        "page",
        "slide",
        "sheet",
        "heading_level",
        "requirement_ids",
        "labels",
        "snapshot_version",
        "source_version",
        "index_contract",
        "text_truncated",
        "ingested_at",
        "kr_doc_id",
        "category",
        "effective_date",
        "expiry_date",
        "jurisdiction",
        "regulations_ref",
        "owner_id",
        "kr_version",
        "row_range",
        "row_count",
        "para_group",
        "char_start",
        "char_end",
        "token_estimate",
        "parent_key",
        "chunk_level",
        "topic_id",
        # TrustOps Index Contract additions (design A §6.4). The five search
        # filter keys must stay in sync with fcrm_policy.search.filters
        # .ALLOWED_METADATA_KEYS (jurisdiction and source_id exist above):
        "corpus_version",
        "trigger_type",
        "risk_area",
        "closed_date",
        # ...plus two stored-only identifiers the clause index writes:
        "clause_id",
        "index_kind",
        # ...and the identifiers and facets of the other two indexes
        # (precedent_cases, controls; design A §6.3):
        "case_id",
        "control_id",
        "controls_version",
        "outcome",
        "band",
    }
)

#: Mongo-style logical operators a filter may use at the top level.
_FILTER_LOGICAL_OPS: frozenset[str] = frozenset({"$and", "$or"})

#: Non-filterable metadata keys — declared at ``create_index``, capped by S3
#: Vectors at ``MAX_NON_FILTERABLE_KEYS`` (Index Contract §Non-filterable metadata).
NON_FILTERABLE_KEYS: tuple[str, ...] = (
    "text",
    "title",
    "section",
    "section_path",
    "hierarchy_path",
    "url",
    "source_path",
    "strategy_reason",
    "columns",
    "comment_range",
)


def _check_non_filterable_cap() -> None:
    if len(NON_FILTERABLE_KEYS) > limits.MAX_NON_FILTERABLE_KEYS:
        raise RuntimeError(
            f"NON_FILTERABLE_KEYS declares {len(NON_FILTERABLE_KEYS)} keys; the backend caps "
            f"non-filterable keys at {limits.MAX_NON_FILTERABLE_KEYS}"
        )


_check_non_filterable_cap()

ALL_METADATA_KEYS: frozenset[str] = FILTERABLE_KEYS | frozenset(NON_FILTERABLE_KEYS)

#: List-valued metadata fields carried as comma-joined strings — never real
#: JSON arrays on the wire. One join convention (``join_list_metadata``) for all.
LIST_METADATA_KEYS: tuple[str, ...] = ("requirement_ids", "labels", "columns")
MAX_LIST_ITEMS = limits.MAX_LIST_METADATA_ITEMS

REQUIRED_METADATA_KEYS: tuple[str, ...] = (
    "doc_id",
    "project",
    "source_type",
    "source_id",
    "content_hash",
    "chunk_key",
    "chunk_index",
    "offset",
    "chunk_strategy",
    "type",
    "snapshot_version",
    "index_contract",
    "text_truncated",
    "text",
)

MAX_META_TEXT_CHARS = limits.MAX_META_TEXT_CHARS
TEXT_FALLBACK_CHARS = limits.TEXT_FALLBACK_CHARS
MAX_METADATA_BYTES = limits.MAX_METADATA_BYTES

#: Step 1 of ``shrink_metadata``: pop these, one at a time, re-measuring before each.
SHRINK_ORDER: tuple[str, ...] = (
    "strategy_reason",
    "hierarchy_path",
    "source_path",
    "labels",
    "columns",
)

#: Step 2: cap these free-text fields to ``SECONDARY_SHRINK_CAP_CHARS`` in this fixed order.
SECONDARY_SHRINK_ORDER: tuple[str, ...] = (
    "title",
    "section",
    "section_path",
    "comment_range",
    "url",
)

#: ``chunk_key``/``parent_key`` tail: ``:{content_hash[:16]}:{chunk_index:04d}``.
_KEY_TAIL_RE = re.compile(r":[0-9a-f]{16}:[0-9]{4}$")
_TOPIC_ID_RE = re.compile(r"^t\d{3,}$")


def doc_id_for(source_type: str, source_id: str) -> str:
    """``doc_id = "{source_type}:{source_id}"`` (Index Contract v2)."""
    return f"{source_type}:{source_id}"


def vector_key(doc_id: str, content_hash: str, chunk_index: int) -> str:
    """Deterministic vector key: ``f"{doc_id}:{content_hash[:16]}:{chunk_index:04d}"``.

    A pure function of document identity, document content and chunk
    position — identical across rebuilds, incremental syncs and reverts;
    changes exactly when the cited text changes. ``content_hash`` may carry
    the ``sha256:`` prefix used elsewhere in the Canonical Document — only
    the hex digest portion is used here.
    """
    digest = content_hash.split(":", 1)[-1]
    return f"{doc_id}:{digest[:16]}:{chunk_index:04d}"


def join_list_metadata(values: Sequence[str] | None) -> str:
    """Comma-join a list-valued metadata field, capped at ``MAX_LIST_ITEMS`` (the one convention)."""
    if not values:
        return ""
    cleaned = [str(v).strip() for v in values if str(v).strip()]
    return ",".join(cleaned[:MAX_LIST_ITEMS])


def split_list_metadata(value: JsonValue) -> list[str]:
    """Inverse of :func:`join_list_metadata` — split a comma-joined string."""
    if not value:
        return []
    if isinstance(value, list | tuple):
        return [str(v).strip() for v in value if str(v).strip()]
    return [part.strip() for part in str(value).split(",") if part.strip()]


def split_metadata(
    metadata: Mapping[str, JsonValue],
) -> tuple[dict[str, JsonValue], dict[str, JsonValue]]:
    """Split a full metadata dict into ``(filterable, non_filterable)``.

    Keys outside both declared sets are dropped silently here — callers
    that need strict conformance call :func:`validate_metadata` first.
    """
    filterable = {k: v for k, v in metadata.items() if k in FILTERABLE_KEYS}
    non_filterable = {k: v for k, v in metadata.items() if k in NON_FILTERABLE_KEYS}
    return filterable, non_filterable


def _is_int(value: JsonValue) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_filter_keys(filters: Mapping[str, object]) -> None:
    """Raise :class:`MetadataContractViolation` if a filter names a non-filterable key.

    TrustOps addition: ``search()``/``get_deep_context()`` take caller filters,
    and a typo'd key would otherwise silently match nothing (S3 Vectors) or
    everything absent (memory). ``$and``/``$or`` are walked recursively.
    """
    for key, cond in filters.items():
        if key in _FILTER_LOGICAL_OPS:
            if not isinstance(cond, Sequence) or isinstance(cond, str):
                raise MetadataContractViolation(f"{key} takes a list of filters")
            for sub in cond:
                if not isinstance(sub, Mapping):
                    raise MetadataContractViolation(f"{key} entries must be filters")
                validate_filter_keys(sub)
        elif key not in FILTERABLE_KEYS:
            raise MetadataContractViolation(
                f"filter key {key!r} is not a filterable Index Contract key"
            )


def validate_metadata(metadata: Mapping[str, JsonValue]) -> None:
    """Raise :class:`MetadataContractViolation` on any Index Contract breach.

    v2: no keys outside ``ALL_METADATA_KEYS`` (schema
    ``additionalProperties: false``); every required key present;
    ``chunk_strategy``/``type``/``source_type`` in the declared enums;
    ``offset == chunk_index``; ``chunk_key`` embeds ``doc_id``; ``text``
    within the ceiling. v2.1 (all optional): ``chunk_level ∈ {0, 1}``;
    ``parent_key`` starts with ``doc_id:`` and has the key tail shape, and
    is never carried by a ``chunk_level=0`` record; ``topic_id`` matches
    ``^t\\d{3,}$``.
    """
    unknown = set(metadata) - ALL_METADATA_KEYS
    if unknown:
        raise MetadataContractViolation(
            f"metadata has keys outside the Index Contract v2 schema: {sorted(unknown)}"
        )
    missing = [k for k in REQUIRED_METADATA_KEYS if k not in metadata]
    if missing:
        raise MetadataContractViolation(f"metadata is missing required keys: {missing}")
    strategy = metadata.get("chunk_strategy")
    if strategy not in CHUNK_STRATEGIES:
        raise MetadataContractViolation(
            f"chunk_strategy={strategy!r} is not one of {CHUNK_STRATEGIES}"
        )
    ctype = metadata.get("type")
    if ctype not in CHUNK_TYPES:
        raise MetadataContractViolation(f"type={ctype!r} is not one of {CHUNK_TYPES}")
    source_type = metadata.get("source_type")
    if source_type not in SOURCE_TYPES:
        raise MetadataContractViolation(
            f"source_type={source_type!r} is not one of {SOURCE_TYPES}"
        )
    if metadata.get("index_contract") != INDEX_CONTRACT_VERSION:
        raise MetadataContractViolation(
            f"index_contract={metadata.get('index_contract')!r} != {INDEX_CONTRACT_VERSION}"
        )
    offset = metadata.get("offset")
    chunk_index = metadata.get("chunk_index")
    if offset != chunk_index:
        raise MetadataContractViolation(
            f"offset ({offset!r}) must equal chunk_index ({chunk_index!r})"
        )
    chunk_key = str(metadata.get("chunk_key", ""))
    doc_id = str(metadata.get("doc_id", ""))
    if doc_id and not chunk_key.startswith(f"{doc_id}:"):
        raise MetadataContractViolation(
            f"chunk_key {chunk_key!r} does not start with doc_id:{doc_id!r}"
        )
    text = metadata.get("text", "")
    if isinstance(text, str) and len(text) > MAX_META_TEXT_CHARS:
        raise MetadataContractViolation(
            f"metadata.text is {len(text)} chars, over the "
            f"{MAX_META_TEXT_CHARS}-char Index Contract v2 ceiling"
        )
    _validate_v21(metadata, doc_id)


def _validate_v21(metadata: Mapping[str, JsonValue], doc_id: str) -> None:
    level = metadata.get("chunk_level")
    if "chunk_level" in metadata and (
        not _is_int(level) or level not in (CHUNK_LEVEL_PARENT, CHUNK_LEVEL_CHILD)
    ):
        raise MetadataContractViolation(
            f"chunk_level={level!r} must be {CHUNK_LEVEL_PARENT} (parent) or "
            f"{CHUNK_LEVEL_CHILD} (child)"
        )
    if "parent_key" in metadata:
        parent_key = metadata.get("parent_key")
        if level == CHUNK_LEVEL_PARENT:
            raise MetadataContractViolation(
                "a chunk_level=0 (parent) record must not carry parent_key"
            )
        if not isinstance(parent_key, str) or not parent_key:
            raise MetadataContractViolation(f"parent_key={parent_key!r} must be a non-empty string")
        if doc_id and not parent_key.startswith(f"{doc_id}:"):
            raise MetadataContractViolation(
                f"parent_key {parent_key!r} does not start with doc_id:{doc_id!r}"
            )
        if _KEY_TAIL_RE.search(parent_key) is None:
            raise MetadataContractViolation(
                f"parent_key {parent_key!r} does not end in ':<hash16>:<index4>'"
            )
        chunk_key = str(metadata.get("chunk_key", ""))
        if parent_key == chunk_key:
            raise MetadataContractViolation("parent_key must not equal the record's own chunk_key")
    if "topic_id" in metadata:
        topic_id = metadata.get("topic_id")
        if not isinstance(topic_id, str) or _TOPIC_ID_RE.match(topic_id) is None:
            raise MetadataContractViolation(f"topic_id={topic_id!r} must match ^t\\d{{3,}}$")


def metadata_size_bytes(metadata: Mapping[str, JsonValue]) -> int:
    """UTF-8 byte size of the JSON-serialised metadata (the backend's measure)."""
    return len(json.dumps(dict(metadata), ensure_ascii=False).encode("utf-8"))


def shrink_metadata(
    metadata: Mapping[str, JsonValue],
    *,
    max_bytes: int = MAX_METADATA_BYTES,
    observer: ContextObserver | None = None,
) -> dict[str, JsonValue]:
    """Trim metadata to fit the backend's per-record byte cap — deterministically.

    1. Pop ``SHRINK_ORDER`` keys one at a time, re-measuring before each.
    2. Cap ``SECONDARY_SHRINK_ORDER`` free-text fields to
       ``SECONDARY_SHRINK_CAP_CHARS`` in that fixed order.
    3. Truncate ``text`` to ``TEXT_FALLBACK_CHARS`` with ``text_truncated=True``
       — ``text`` is **never** popped.
    4. Still over the cap → ``MetadataTooLarge`` (the document fails loudly).

    A trimmed record is reported to ``observer`` as a
    ``GuardrailEvent(name="metadata_shrink", outcome="corrected")``.
    """
    meta: dict[str, JsonValue] = dict(metadata)
    if metadata_size_bytes(meta) <= max_bytes:
        return meta

    popped: list[str] = []
    for key in SHRINK_ORDER:
        if metadata_size_bytes(meta) <= max_bytes:
            break
        if key in meta:
            meta.pop(key)
            popped.append(key)

    capped: list[str] = []
    if metadata_size_bytes(meta) > max_bytes:
        for key in SECONDARY_SHRINK_ORDER:
            value = meta.get(key)
            if isinstance(value, str) and len(value) > limits.SECONDARY_SHRINK_CAP_CHARS:
                meta[key] = value[: limits.SECONDARY_SHRINK_CAP_CHARS]
                capped.append(key)
            if metadata_size_bytes(meta) <= max_bytes:
                break

    text_trim = ""
    if metadata_size_bytes(meta) > max_bytes:
        text = str(meta.get("text", ""))
        if len(text) > TEXT_FALLBACK_CHARS:
            meta["text"] = text[:TEXT_FALLBACK_CHARS]
            meta["text_truncated"] = True
            text_trim = f"text {len(text)}->{TEXT_FALLBACK_CHARS}"

    size = metadata_size_bytes(meta)
    if size > max_bytes:
        raise MetadataTooLarge(
            f"metadata for chunk_key={meta.get('chunk_key')!r} is still {size} bytes after "
            f"every trim (cap {max_bytes}); document fails loudly per Index Contract v2 "
            "rather than silently dropping text."
        )
    if observer is not None:
        detail_parts = [p for p in (
            f"popped {','.join(popped)}" if popped else "",
            f"capped {','.join(capped)}" if capped else "",
            text_trim,
        ) if p]
        observer.on_guardrail_event(
            GuardrailEvent(
                name="metadata_shrink",
                subject=str(meta.get("chunk_key") or meta.get("doc_id") or ""),
                outcome="corrected",
                detail="; ".join(detail_parts) + f" -> {size} bytes (cap {max_bytes})",
            )
        )
    return meta


def content_hash_of(data: bytes) -> str:
    """``"sha256:" + sha256(data)`` (Canonical Document content_hash shape)."""
    return "sha256:" + hashlib.sha256(data).hexdigest()
