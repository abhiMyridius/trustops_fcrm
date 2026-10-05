"""Diff-based change detection and state management.

Compares current extracted input against the previous state manifest
to produce a ChangeSet (new / updated / deleted / unchanged).
"""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from evoq_context.core.errors import StateCorrupt
from evoq_context.core.models import ChangeSet, ExtractedItem

logger = logging.getLogger(__name__)


def load_previous_state(state_path: Path) -> dict[str, str]:
    """Load the doc_id -> content_hash map from state.json.

    A **missing** file means "first run" — not an error — and returns
    ``{}``. A **present but unparseable** file (or one with the wrong
    shape) raises :class:`~evoq_context.core.errors.StateCorrupt`: a build
    must never silently treat corrupt state as "nothing has changed yet"
    (that would either skip real work or, worse, re-embed everything under
    the illusion nothing existed before) — the caller decides how to
    surface that loudly.
    """
    if not state_path.exists():
        logger.info("No previous state found — treating as first run")
        return {}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise StateCorrupt(
            f"{state_path} exists but could not be parsed as JSON: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise StateCorrupt(f"{state_path} does not contain a JSON object")
    doc_hash_index = data.get("doc_hash_index", {})
    if not isinstance(doc_hash_index, dict):
        raise StateCorrupt(f"{state_path} has a non-object 'doc_hash_index'")
    return doc_hash_index


def compute_current_state(
    items: list[ExtractedItem],
) -> dict[str, str]:
    """Build doc_id -> content_hash from the current input batch.

    Ensures every item has a hash computed before returning.
    """
    result: dict[str, str] = {}
    for item in items:
        item.ensure_hash()
        result[item.doc_id] = item.content_hash
    return result


def reconcile(
    previous: dict[str, str],
    current: dict[str, str],
) -> ChangeSet:
    """Diff previous vs current and produce a ChangeSet."""
    new_ids = [
        doc_id for doc_id in current if doc_id not in previous
    ]
    deleted_ids = [
        doc_id for doc_id in previous if doc_id not in current
    ]
    updated_ids = [
        doc_id
        for doc_id in current
        if doc_id in previous
        and current[doc_id] != previous[doc_id]
    ]
    unchanged = (
        len(current) - len(new_ids) - len(updated_ids)
    )

    logger.info(
        "Reconciled: %d new, %d updated, %d deleted, %d unchanged",
        len(new_ids),
        len(updated_ids),
        len(deleted_ids),
        unchanged,
    )

    return ChangeSet(
        new_ids=new_ids,
        updated_ids=updated_ids,
        deleted_ids=deleted_ids,
        unchanged_count=unchanged,
    )


def compute_state_version_key(hash_map: dict[str, str]) -> str:
    """Deterministic version key from the doc hash index.

    SHA-256 of sorted ``doc_id:content_hash`` pairs.
    """
    if not hash_map:
        return hashlib.sha256(b"empty").hexdigest()
    pairs = sorted(hash_map.items())
    payload = "|".join(f"{k}:{v}" for k, v in pairs)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def save_state(
    state_path: Path,
    *,
    project: str,
    run_id: str,
    doc_hash_index: dict[str, str],
    state_version_key: str,
    build_version_key: str,
    goal_prompt_hash: str = "",
    config_fingerprint: str = "",
    index_fingerprint: str = "",
    analysis_fingerprint: str = "",
    artifacts: dict[str, str] | None = None,
) -> None:
    """Overwrite state.json with the current manifest.

    ``index_fingerprint``/``analysis_fingerprint`` are the raw, *split*
    fingerprints (``ContextConfig.index_fingerprint()`` /
    ``.analysis_fingerprint()``) saved alongside ``build_version_key``
    (which only stores a single combined/hashed fingerprint and so can't
    be compared back against a freshly computed one) — this is what lets
    ``build_context()`` tell an index-affecting config change (needs a
    full re-embed under a new snapshot version) apart from an
    analysis-only one (re-synthesize in place).

    ``artifacts`` records the per-artifact outcome of this run (keys such
    as ``context_md``/``document_inventory``/``index_manifest``, values
    ``"ok"``/``"skipped"``/``"failed"``) so a partial run's ``state.json``
    accurately reflects what actually landed on disk — this is what lets a
    later build detect ``artifacts_missing``/not-``"ok"`` and self-repair
    via ``mode="resynthesize"`` instead of treating the run as complete.
    """
    state_path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "project": project,
        "run_id": run_id,
        "state_version_key": state_version_key,
        "build_version_key": build_version_key,
        "documents_total": len(doc_hash_index),
        "doc_hash_index": doc_hash_index,
    }
    if goal_prompt_hash:
        payload["goal_prompt_hash"] = goal_prompt_hash
    if config_fingerprint:
        payload["config_fingerprint"] = config_fingerprint
    if index_fingerprint:
        payload["index_fingerprint"] = index_fingerprint
    if analysis_fingerprint:
        payload["analysis_fingerprint"] = analysis_fingerprint
    if artifacts:
        payload["artifacts"] = dict(artifacts)
    state_path.write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    logger.info("State saved -> %s", state_path)


def read_artifact_statuses_from_state(state_path: Path) -> dict[str, str]:
    """Return the ``artifacts`` map saved by the previous build.

    Absent/corrupt/first-run all resolve to ``{}`` (never raises) — this is
    purely advisory input to ``BuildPlanner``'s ``artifacts_missing`` check,
    which already treats "nothing recorded" as "missing".
    """
    if not state_path.exists():
        return {}
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    artifacts = payload.get("artifacts", {})
    return artifacts if isinstance(artifacts, dict) else {}


def read_fingerprints_from_state(state_path: Path) -> tuple[str, str]:
    """Return the fingerprints saved by the previous build.

    ``(index_fingerprint, analysis_fingerprint)``, or ``("", "")`` if
    absent/corrupt/first run.
    """
    if not state_path.exists():
        return "", ""
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "", ""
    return (
        str(payload.get("index_fingerprint", "")).strip(),
        str(payload.get("analysis_fingerprint", "")).strip(),
    )


def write_current_pointer(pointer_path: Path, version: str) -> None:
    """Write the active snapshot label (e.g. v2) to current.txt."""
    pointer_path.parent.mkdir(parents=True, exist_ok=True)
    pointer_path.write_text(f"{version.strip()}\n", encoding="utf-8")


def compute_build_version_key(
    state_version_key: str,
    config_fingerprint: str,
) -> str:
    """Version key that also captures processing config changes.

    ``config_fingerprint`` should encode embedding model, chunk
    settings, analysis model, etc.
    """
    combined = f"{state_version_key}|{config_fingerprint}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
