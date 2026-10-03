"""``BuildPlanner`` — decide *what kind* of build this run is, before any stage runs.

Scope & functionality
----------------------
Everything that used to be interleaved, ad-hoc, at the top of the old
monolithic ``build_context()`` — loading the previous ``state.json``,
content-hash reconciliation, index/analysis config-drift detection, goal
prompt change detection — is computed once, up front, into one immutable
:class:`BuildPlan`. The plan's ``mode`` then drives which stages
``pipeline/build.py`` assembles:

- ``"drift_error"``: index-affecting config changed but
  ``REINDEX_ON_CONFIG_CHANGE=false`` — refuse, no stages run, no writes.
- ``"noop"``: no document changes, no config drift, same goal prompt, and
  the previous snapshot's artifacts are intact — nothing to do.
- ``"resynthesize"``: no document *content* changes, but either the goal
  prompt or the analysis-only config changed, **or** the previous
  snapshot's ``context.md``/``index_manifest.json`` is missing/unreadable
  (``artifacts_missing``) — re-run analysis/render in place, no new
  snapshot version. ``artifacts_missing`` is the self-repair fix for a run
  that previously died mid-render: without it, a build with genuinely no
  new document changes would keep reporting a stale "noop" forever even
  though the active snapshot never finished writing its narrative.
- ``"full"``: real document changes, or a forced reindex — write a new
  ``vN`` snapshot version.

A corrupt (present-but-unparseable) ``state.json`` raises
:class:`~evoq_context.core.errors.StateCorrupt` out of :meth:`plan` — the
caller (``pipeline/build.py``) turns that into a loud ``error`` result
rather than silently treating corrupt state as "first run".
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.errors import ManifestCorrupt
from evoq_context.core.models import ChangeSet, ExtractedItem
from evoq_context.state.reconciler import (
    compute_current_state,
    load_previous_state,
    read_artifact_statuses_from_state,
    read_fingerprints_from_state,
    reconcile,
)
from evoq_context.storage import snapshot as _snapshot
from evoq_context.storage.layout import SnapshotLayout

logger = logging.getLogger(__name__)

BuildMode = Literal["noop", "resynthesize", "drift_error", "full"]

CONFIG_DRIFT_REINDEX_REQUIRED = "CONFIG_DRIFT_REINDEX_REQUIRED"


def _normalize_goal_prompt(goal_prompt: str) -> str:
    return " ".join((goal_prompt or "").split()).strip()


def _goal_prompt_hash(goal_prompt: str) -> str:
    normalized = _normalize_goal_prompt(goal_prompt)
    if not normalized:
        return ""
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _read_goal_prompt_hash_from_state(state_path: Path) -> str:
    if not state_path.exists():
        return ""
    try:
        payload = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    return str(payload.get("goal_prompt_hash", "")).strip()


def _reconcile_hashes_and_changeset(
    previous: dict[str, str], items: list[ExtractedItem]
) -> tuple[dict[str, str], ChangeSet]:
    """CPU / hashing work for reconciliation (safe for a thread pool)."""
    current = compute_current_state(items)
    return current, reconcile(previous, current)


@dataclass(frozen=True)
class BuildPlan:
    """Everything the stage list needs to know before any stage runs."""

    mode: BuildMode
    active_label: str
    to_label: str
    tmp_label: str
    changeset: ChangeSet
    current_hashes: dict[str, str]
    previous_hashes: dict[str, str]
    previous_vector_manifest: dict[str, dict[str, Any]]
    index_drift: bool
    analysis_drift: bool
    prompt_changed: bool
    prompt_hash: str
    normalized_goal_prompt: str
    index_fingerprint_now: str
    analysis_fingerprint_now: str
    artifacts_missing: bool
    reindex_reason: str = ""
    drift_message: str = ""


class BuildPlanner:
    """Computes one :class:`BuildPlan` for a build request."""

    def __init__(self, config: ContextConfig, layout: SnapshotLayout | None = None) -> None:
        """Bind to ``config`` (and its layout, or an override for tests)."""
        self._config = config
        self._layout = layout or config.layout

    def _artifacts_missing(self, project: str, active_label: str) -> bool:
        """Detect a missing/unreadable ``context.md`` or ``index_manifest.json``.

        Forces ``mode="resynthesize"`` so a run that previously died
        mid-render self-repairs on the next build, regardless of whether
        any document content changed.
        """
        if not active_label:
            return False
        # TrustOps: with synthesis off, context.md is never written, so its
        # absence is not a failed render to self-repair.
        if self._config.synthesis_enabled and self._context_md_missing(project, active_label):
            return True
        return self._index_manifest_missing(project, active_label)

    def _context_md_missing(self, project: str, active_label: str) -> bool:
        """True when ``context.md`` is absent or its recorded render status is not ok."""
        context_md = self._layout.resolve_context_md_path(project, version=active_label)
        if not context_md.is_file():
            return True
        # Defence in depth: a crash mid-write can leave a file that exists
        # but was never finished (context.md/document_inventory.md are
        # plain writes, not temp+os.replace) — the previous run's own
        # recorded artifact status catches that even when the file itself
        # still looks present.
        recorded = read_artifact_statuses_from_state(
            self._layout.state_path(project, version=active_label)
        )
        return recorded.get("context_md") not in (None, "ok")

    def _index_manifest_missing(self, project: str, active_label: str) -> bool:
        """True when the active snapshot's ``index_manifest.json`` is missing/unreadable."""
        if (self._config.vector_backend or "").strip().lower() == "none":
            return False
        try:
            manifest = _snapshot.read_index_manifest(
                self._layout.index_manifest_path(project, version=active_label)
            )
        except ManifestCorrupt:
            return True
        if manifest is None:
            return True
        recorded = read_artifact_statuses_from_state(
            self._layout.state_path(project, version=active_label)
        )
        return recorded.get("index_manifest") not in (None, "ok")

    def plan(self, request: ContextBuildRequest) -> BuildPlan:
        """Compute the :class:`BuildPlan` for ``request``.

        Sync — callers that want this off the event loop should run it in
        a thread, since every read here is local disk I/O.
        """
        config = self._config
        project = request.project
        normalized_goal_prompt = _normalize_goal_prompt(request.goal_prompt)
        prompt_hash = _goal_prompt_hash(normalized_goal_prompt)

        config.migrate_flat_layout_to_v1(project)
        active_label = self._layout.read_current_version_label(project) or ""
        state_load_version = active_label if active_label else "v1"
        state_load_path = self._layout.state_path(project, version=state_load_version)

        previous_prompt_hash = _read_goal_prompt_hash_from_state(state_load_path)
        previous_index_fp, previous_analysis_fp = read_fingerprints_from_state(state_load_path)
        index_fingerprint_now = config.index_fingerprint()
        analysis_fingerprint_now = config.analysis_fingerprint()
        index_drift = bool(previous_index_fp) and previous_index_fp != index_fingerprint_now
        analysis_drift = (
            bool(previous_analysis_fp) and previous_analysis_fp != analysis_fingerprint_now
        )

        previous_hashes = load_previous_state(state_load_path)
        current_hashes, changeset = _reconcile_hashes_and_changeset(
            previous_hashes, request.extracted_items
        )

        previous_vector_manifest: dict[str, dict[str, Any]] = {}
        if active_label:
            previous_vector_manifest = _snapshot.read_vector_manifest(
                self._layout.vector_manifest_path(project, version=active_label)
            )

        prompt_changed = (
            bool(prompt_hash) and bool(previous_prompt_hash) and prompt_hash != previous_prompt_hash
        )
        if not previous_prompt_hash and bool(prompt_hash):
            prompt_changed = True

        if index_drift and not config.reindex_on_config_change:
            return BuildPlan(
                mode="drift_error",
                active_label=active_label,
                to_label=active_label or "",
                tmp_label="",
                changeset=changeset,
                current_hashes=current_hashes,
                previous_hashes=previous_hashes,
                previous_vector_manifest=previous_vector_manifest,
                index_drift=index_drift,
                analysis_drift=analysis_drift,
                prompt_changed=prompt_changed,
                prompt_hash=prompt_hash,
                normalized_goal_prompt=normalized_goal_prompt,
                index_fingerprint_now=index_fingerprint_now,
                analysis_fingerprint_now=analysis_fingerprint_now,
                artifacts_missing=False,
                drift_message=(
                    "Embedding/chunking configuration changed since the last build "
                    "(index_fingerprint drift) but REINDEX_ON_CONFIG_CHANGE=false — "
                    "refusing to mix old and new chunking under the same index. Set "
                    "REINDEX_ON_CONFIG_CHANGE=true to reindex, or revert the config change."
                ),
            )

        reindex_reason = ""
        if index_drift and config.reindex_on_config_change:
            # Full reindex: every currently-provided document is treated as
            # changed regardless of content-hash match, so it is
            # re-chunked/re-embedded under the new config; the stale index
            # is dropped rather than mixed with the new one.
            changeset = ChangeSet(
                new_ids=changeset.new_ids,
                updated_ids=sorted(set(current_hashes) - set(changeset.new_ids)),
                deleted_ids=changeset.deleted_ids,
                unchanged_count=0,
            )
            reindex_reason = "index_config_changed"

        artifacts_missing = self._artifacts_missing(project, active_label)

        # TrustOps: a goal-prompt or analysis-config change only matters when
        # synthesis runs; with it off such a build is a noop, not a resynthesize.
        synthesis_inputs_changed = self._config.synthesis_enabled and (
            prompt_changed or analysis_drift
        )
        no_content_or_config_change = (
            not changeset.has_changes and not synthesis_inputs_changed
        )
        if no_content_or_config_change and not artifacts_missing:
            mode: BuildMode = "noop"
        elif not changeset.has_changes:
            mode = "resynthesize"
        else:
            mode = "full"

        to_label = active_label or ""
        tmp_label = ""
        if mode == "full":
            to_label = SnapshotLayout.bump_version_label(active_label)
            tmp_label = f"{to_label}.tmp"
        elif mode == "resynthesize":
            to_label = active_label or "v1"

        return BuildPlan(
            mode=mode,
            active_label=active_label,
            to_label=to_label,
            tmp_label=tmp_label,
            changeset=changeset,
            current_hashes=current_hashes,
            previous_hashes=previous_hashes,
            previous_vector_manifest=previous_vector_manifest,
            index_drift=index_drift,
            analysis_drift=analysis_drift,
            prompt_changed=prompt_changed,
            prompt_hash=prompt_hash,
            normalized_goal_prompt=normalized_goal_prompt,
            index_fingerprint_now=index_fingerprint_now,
            analysis_fingerprint_now=analysis_fingerprint_now,
            artifacts_missing=artifacts_missing,
            reindex_reason=reindex_reason,
        )
