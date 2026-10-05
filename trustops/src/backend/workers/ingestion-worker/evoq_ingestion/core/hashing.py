"""Identity & change-detection hashing — Canonical Document contract §1.1.

Scope & functionality
---------------------
Pure functions, no I/O. These implement the exact algorithms pinned in
``AEO/contracts/canonical_document.md`` so that ``source_id`` is stable
across syncs/versions and ``content_hash`` changes exactly when the
rendered Markdown body changes (whitespace-insensitive to line endings).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any


def canonicalize(text: str) -> str:
    """CRLF -> LF, rstrip every line, exactly one trailing newline."""
    normalized = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")
    stripped = [line.rstrip() for line in lines]
    # Drop trailing empty lines introduced by a final "\n", then add exactly one.
    while stripped and stripped[-1] == "":
        stripped.pop()
    return "\n".join(stripped) + "\n" if stripped else "\n"


def compute_content_hash(body_markdown: str) -> str:
    """``"sha256:" + sha256(canonicalize(body_markdown)).hexdigest()``."""
    digest = hashlib.sha256(canonicalize(body_markdown).encode()).hexdigest()
    return f"sha256:{digest}"


def compute_source_id(origin_id: str, source_key: str) -> str:
    """``"SRC" + sha256(f"{origin_id}:{source_key}").hexdigest()[:32]``.

    Stable across syncs and versions — a pure function of identity only.
    """
    digest = hashlib.sha256(f"{origin_id}:{source_key}".encode()).hexdigest()
    return f"SRC{digest[:32]}"


def compute_metadata_hash(
    *,
    labels: Sequence[str] | None,
    hierarchy: Mapping[str, Any] | None,
    authors: Sequence[Mapping[str, Any]] | None,
    source_version: str | int | None,
    metadata: Mapping[str, Any] | None,
) -> str:
    """Hash over the fields that trigger an in-place metadata refresh.

    ``"sha256:" + sha256(json.dumps({...}, sort_keys=True, default=str))``.
    """
    payload = {
        "labels": labels or [],
        "hierarchy": hierarchy or {},
        "authors": authors or [],
        "source_version": source_version,
        "metadata": metadata or {},
    }
    serialized = json.dumps(payload, sort_keys=True, default=str)
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def build_source_key(source_type: str, *, page_id: str | None = None,
                      issue_id: str | None = None, doc_id: str | None = None,
                      file_name: str | None = None) -> str:
    """The natural id per §1.1.

    ``confluence:{page_id}`` / ``jira:{issue_id}`` / ``file:{doc_id}`` /
    ``file:name:{file_name}``.
    """
    if source_type == "confluence":
        if not page_id:
            raise ValueError("confluence source_key requires page_id")
        return f"confluence:{page_id}"
    if source_type == "jira":
        if not issue_id:
            raise ValueError("jira source_key requires issue_id")
        return f"jira:{issue_id}"
    if source_type == "file":
        if doc_id:
            return f"file:{doc_id}"
        if file_name:
            return f"file:name:{file_name}"
        raise ValueError("file source_key requires doc_id or file_name")
    raise ValueError(f"Unsupported source_type for source_key: {source_type!r}")
