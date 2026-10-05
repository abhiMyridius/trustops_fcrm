"""Config-drift reindex behaviour (plan §3.5).

Index-affecting config drift (embedding/chunking) with
REINDEX_ON_CONFIG_CHANGE=true forces a full re-embed under a new snapshot
version; with it false, the build refuses and returns
CONFIG_DRIFT_REINDEX_REQUIRED. Analysis-only drift never bumps the
snapshot version — it re-synthesizes the existing one in place.
"""

from __future__ import annotations

import asyncio
import dataclasses

from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.models import ExtractedItem
from evoq_context.pipeline.build import CONFIG_DRIFT_REINDEX_REQUIRED, build_context


def _items() -> list[ExtractedItem]:
    return [
        ExtractedItem(
            source_type="file",
            source_id="F1",
            title="Doc",
            body="Some document body text.",
        ),
    ]


def test_index_drift_with_reindex_allowed_bumps_version_and_reembeds(
    dummy_config: ContextConfig,
) -> None:
    req = ContextBuildRequest(
        project="proj", goal_prompt="goal", extracted_items=_items()
    )
    first = asyncio.run(build_context(req, dummy_config))
    assert first.status == "success"
    assert first.version == "v1"

    drifted = dataclasses.replace(
        dummy_config,
        chunk_target_tokens=dummy_config.chunk_target_tokens + 1,
        reindex_on_config_change=True,
    )
    second = asyncio.run(build_context(req, drifted))
    assert second.status == "success"
    assert second.version == "v2"
    assert second.stats.get("reindex_reason") == "index_config_changed"
    assert second.stats["vectors_upserted"] > 0


def test_index_drift_with_reindex_disallowed_returns_config_drift_error(
    dummy_config: ContextConfig,
) -> None:
    req = ContextBuildRequest(
        project="proj", goal_prompt="goal", extracted_items=_items()
    )
    first = asyncio.run(build_context(req, dummy_config))
    assert first.status == "success"

    drifted = dataclasses.replace(
        dummy_config,
        chunk_target_tokens=dummy_config.chunk_target_tokens + 1,
        reindex_on_config_change=False,
    )
    second = asyncio.run(build_context(req, drifted))
    assert second.status == "error"
    assert second.errors
    assert second.errors[0].code == CONFIG_DRIFT_REINDEX_REQUIRED
    # No new version was written — the pointer is unchanged.
    assert second.version == "v1" or second.version == ""
    assert drifted.read_current_version_label("proj") == "v1"


def test_analysis_only_drift_resynthesizes_in_place_no_new_version(
    dummy_config: ContextConfig,
) -> None:
    req = ContextBuildRequest(
        project="proj", goal_prompt="goal", extracted_items=_items()
    )
    first = asyncio.run(build_context(req, dummy_config))
    assert first.version == "v1"

    analysis_drifted = dataclasses.replace(
        dummy_config,
        synthesis_max_tokens=dummy_config.synthesis_max_tokens + 1,
    )
    second = asyncio.run(build_context(req, analysis_drifted))
    assert second.status == "success"
    # Same snapshot version — analysis-only drift never bumps it.
    assert second.version == "v1"


def test_no_drift_no_changes_is_a_pure_noop(dummy_config: ContextConfig) -> None:
    req = ContextBuildRequest(
        project="proj", goal_prompt="goal", extracted_items=_items()
    )
    first = asyncio.run(build_context(req, dummy_config))
    second = asyncio.run(build_context(req, dummy_config))
    assert second.version == first.version == "v1"
    assert second.stats["unchanged"] == 1
