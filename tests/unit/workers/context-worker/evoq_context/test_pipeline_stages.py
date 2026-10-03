"""Stage-based pipeline architecture (plan §2.5): ``BuildPlanner`` mode
selection (including the ``artifacts_missing`` self-repair fix) and a few
stages exercised in isolation with mocked collaborators. Full end-to-end
coverage of each ``BuildPlan.mode`` already lives in
``test_build_happy_paths.py``/``test_reindex_on_config_change.py`` — this
file focuses on the planner's decision logic and stage-level behaviour that
those don't reach directly.
"""

from __future__ import annotations

import asyncio

from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.models import ExtractedItem
from evoq_context.pipeline.build import build_context
from evoq_context.pipeline.context import BuildContext
from evoq_context.pipeline.plan import BuildPlanner
from evoq_context.pipeline.stages import ManifestStage, PublishStage, SeedSnapshotStage
from evoq_context.pipeline.tracker import PipelineTracker


def _items() -> list[ExtractedItem]:
    return [
        ExtractedItem(
            source_type="file", source_id="F1", title="Doc", body="Some body text."
        )
    ]


def _request() -> ContextBuildRequest:
    return ContextBuildRequest(
        project="proj", goal_prompt="goal", extracted_items=_items()
    )


# ---------------------------------------------------------------------------
# BuildPlanner.plan() — mode selection
# ---------------------------------------------------------------------------


def test_plan_mode_full_on_first_build(dummy_config: ContextConfig) -> None:
    plan = BuildPlanner(dummy_config).plan(_request())
    assert plan.mode == "full"
    assert plan.active_label == ""
    assert plan.to_label == "v1"
    assert plan.tmp_label == "v1.tmp"


def test_plan_mode_noop_when_nothing_changed(dummy_config: ContextConfig) -> None:
    asyncio.run(build_context(_request(), dummy_config))
    plan = BuildPlanner(dummy_config).plan(_request())
    assert plan.mode == "noop"
    assert plan.artifacts_missing is False


def test_plan_mode_resynthesize_on_prompt_change_only(
    dummy_config: ContextConfig,
) -> None:
    asyncio.run(build_context(_request(), dummy_config))
    changed_prompt = ContextBuildRequest(
        project="proj", goal_prompt="a different goal", extracted_items=_items()
    )
    plan = BuildPlanner(dummy_config).plan(changed_prompt)
    assert plan.mode == "resynthesize"
    assert plan.to_label == "v1"
    assert plan.tmp_label == ""


def test_plan_mode_full_on_changed_document(dummy_config: ContextConfig) -> None:
    asyncio.run(build_context(_request(), dummy_config))
    changed = ContextBuildRequest(
        project="proj",
        goal_prompt="goal",
        extracted_items=[
            ExtractedItem(
                source_type="file", source_id="F1", title="Doc", body="Different body."
            )
        ],
    )
    plan = BuildPlanner(dummy_config).plan(changed)
    assert plan.mode == "full"
    assert plan.to_label == "v2"


def test_plan_mode_drift_error_when_reindex_disallowed(
    dummy_config: ContextConfig,
) -> None:
    import dataclasses

    asyncio.run(build_context(_request(), dummy_config))
    drifted = dataclasses.replace(
        dummy_config,
        chunk_target_tokens=dummy_config.chunk_target_tokens + 1,
        reindex_on_config_change=False,
    )
    plan = BuildPlanner(drifted).plan(_request())
    assert plan.mode == "drift_error"
    assert plan.drift_message


def test_plan_artifacts_missing_forces_resynthesize_when_context_md_deleted(
    dummy_config: ContextConfig,
) -> None:
    """The self-repair fix: a run that previously died mid-render (or
    whose context.md was otherwise lost) must not be mistaken for a clean
    no-op just because no document content changed."""
    asyncio.run(build_context(_request(), dummy_config))
    md_path = dummy_config.resolve_context_md_path("proj", version="v1")
    assert md_path.is_file()
    md_path.unlink()

    plan = BuildPlanner(dummy_config).plan(_request())
    assert plan.artifacts_missing is True
    assert plan.mode == "resynthesize"

    # And an actual build_context call self-repairs it.
    result = asyncio.run(build_context(_request(), dummy_config))
    assert result.status == "success"
    assert result.version == "v1"
    assert md_path.is_file()


def test_plan_artifacts_missing_forces_resynthesize_when_index_manifest_deleted(
    dummy_config: ContextConfig,
) -> None:
    asyncio.run(build_context(_request(), dummy_config))
    manifest_path = dummy_config.index_manifest_path("proj", version="v1")
    assert manifest_path.is_file()
    manifest_path.unlink()

    plan = BuildPlanner(dummy_config).plan(_request())
    assert plan.artifacts_missing is True
    assert plan.mode == "resynthesize"


def test_plan_artifacts_missing_false_when_no_vector_backend_and_context_md_present(
    tmp_path,
) -> None:
    config = ContextConfig(
        projects_root=tmp_path,
        vector_backend="none",
        s3v_bucket="",
        s3v_region="us-east-1",
        s3v_index_prefix="context",
        embedding_model="amazon.titan-embed-text-v2:0",
        embedding_provider="dummy",
        embedding_dimension=8,
        embedding_max_input_tokens=50_000,
        embedding_input_safety=1.0,
        chunk_target_tokens=700,
        chunk_hard_max_tokens=2000,
        chunk_max_rows=500,
        llm_max_tokens=15000,
        analysis_model="dummy",
        analysis_provider="dummy",
        synthesis_model="dummy",
        synthesis_provider="dummy",
    )
    asyncio.run(build_context(_request(), config))
    plan = BuildPlanner(config).plan(_request())
    assert plan.artifacts_missing is False
    assert plan.mode == "noop"


def test_state_corrupt_state_json_returns_error_status(
    dummy_config: ContextConfig,
) -> None:
    asyncio.run(build_context(_request(), dummy_config))
    state_path = dummy_config.state_path("proj", version="v1")
    state_path.write_text("{not valid json", encoding="utf-8")

    result = asyncio.run(build_context(_request(), dummy_config))
    assert result.status == "error"
    assert any(e.code == "STATE_CORRUPT" for e in result.errors)
    # No new version was written.
    assert dummy_config.read_current_version_label("proj") == "v1"


# ---------------------------------------------------------------------------
# Stages in isolation
# ---------------------------------------------------------------------------


def _ctx(config: ContextConfig, plan) -> BuildContext:
    tracker = PipelineTracker()
    return BuildContext(
        request=_request(),
        config=config,
        plan=plan,
        tracker=tracker,
        llm=None,
        vector_store=None,
    )


def test_publish_stage_skips_for_non_full_mode(dummy_config: ContextConfig) -> None:
    asyncio.run(build_context(_request(), dummy_config))
    plan = BuildPlanner(dummy_config).plan(
        ContextBuildRequest(
            project="proj", goal_prompt="different", extracted_items=_items()
        )
    )
    assert plan.mode == "resynthesize"
    ctx = _ctx(dummy_config, plan)
    asyncio.run(PublishStage().run(ctx))
    statuses = {c.name: c.status for c in ctx.tracker.components}
    assert statuses["publish"] == "skipped"


def test_seed_snapshot_stage_skips_when_no_previous_snapshot(
    dummy_config: ContextConfig,
) -> None:
    plan = BuildPlanner(dummy_config).plan(_request())
    assert plan.mode == "full"
    assert plan.active_label == ""
    ctx = _ctx(dummy_config, plan)
    asyncio.run(SeedSnapshotStage().run(ctx))
    statuses = {c.name: c.status for c in ctx.tracker.components}
    assert statuses["seed_snapshot"] == "skipped"


def test_manifest_stage_skips_when_no_vector_backend(tmp_path) -> None:
    config = ContextConfig(
        projects_root=tmp_path,
        vector_backend="none",
        s3v_bucket="",
        s3v_region="us-east-1",
        s3v_index_prefix="context",
        embedding_model="amazon.titan-embed-text-v2:0",
        embedding_provider="dummy",
        embedding_dimension=8,
        embedding_max_input_tokens=50_000,
        embedding_input_safety=1.0,
        chunk_target_tokens=700,
        chunk_hard_max_tokens=2000,
        chunk_max_rows=500,
        llm_max_tokens=15000,
        analysis_model="dummy",
        analysis_provider="dummy",
        synthesis_model="dummy",
        synthesis_provider="dummy",
    )
    plan = BuildPlanner(config).plan(_request())
    ctx = _ctx(config, plan)
    ctx.vector_store = None
    asyncio.run(ManifestStage().run(ctx))
    statuses = {c.name: c.status for c in ctx.tracker.components}
    assert statuses["manifest"] == "skipped"
    assert ctx.index_manifest is None
