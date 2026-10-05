from __future__ import annotations

import asyncio

import evoq_context.pipeline.build as build_module
import evoq_context.pipeline.plan as plan_module
import evoq_context.pipeline.stages as stages_module
from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.models import ExtractedItem
from evoq_context.pipeline.build import build_context
from evoq_context.pipeline.vectors import VectorWriter
from evoq_context.synthesis.analyzer import analyze_corpus
from evoq_context.synthesis.context_md import generate_context_md


def _request(items: list[ExtractedItem]) -> ContextBuildRequest:
    return ContextBuildRequest(
        project="proj",
        goal_prompt="Generate goals",
        extracted_items=items,
    )


def test_reconcile_failure_returns_error_status(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
    monkeypatch,
) -> None:
    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("reconcile exploded")

    # Reconciliation happens during planning now (BuildPlanner needs the
    # changeset to decide the run's mode before any stage can run).
    monkeypatch.setattr(plan_module, "_reconcile_hashes_and_changeset", _boom)
    result = asyncio.run(build_context(_request(sample_items), dummy_config))

    assert result.status == "error"
    assert result.errors
    assert result.errors[0].stage == "reconcile"
    assert result.errors[0].code == "RECONCILE_ERROR"


def test_store_write_failure_becomes_partial_success(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
    monkeypatch,
) -> None:
    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("store exploded")

    # LocalContentStore is one class, imported by both build.py (for
    # backward-compatible monkeypatching) and stages.py (where it's
    # actually called) — patching the class attribute is global.
    monkeypatch.setattr(build_module.LocalContentStore, "add", _boom)
    result = asyncio.run(build_context(_request(sample_items), dummy_config))

    assert result.status == "partial_success"
    assert any(err.stage == "store_write" for err in result.errors)
    comp = {c.name: c.status for c in result.component_statuses}
    assert comp["store_write"] == "error"


def test_vector_write_failure_reports_component_error(
    tmp_path,
    sample_items: list[ExtractedItem],
    monkeypatch,
) -> None:
    config = ContextConfig(
        projects_root=tmp_path,
        vector_backend="memory",
        s3v_bucket="bucket",
        s3v_region="us-east-1",
        s3v_index_prefix="context",
        embedding_model="amazon.titan-embed-text-v2:0",
        embedding_provider="dummy",
        embedding_dimension=8,
        embedding_max_input_tokens=50_000,
        embedding_input_safety=1.0,
        chunk_target_tokens=5000,
        chunk_hard_max_tokens=50_000,
        chunk_max_rows=500,
        llm_max_tokens=15000,
        analysis_model="dummy",
        analysis_provider="dummy",
        synthesis_model="dummy",
        synthesis_provider="dummy",
    )

    async def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("vector exploded")

    monkeypatch.setattr(VectorWriter, "write", _boom)
    result = asyncio.run(build_context(_request(sample_items), config))

    assert result.status in {"partial_success", "error"}
    assert any(err.stage == "vector_write" for err in result.errors)


def test_analysis_failure_falls_back_to_goal_prompt(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
    monkeypatch,
) -> None:
    async def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("analysis exploded")

    monkeypatch.setattr(stages_module, "analyze_corpus", _boom)
    result = asyncio.run(build_context(_request(sample_items), dummy_config))

    assert any(err.stage == "analysis" for err in result.errors)
    assert result.goals
    assert "Generate goals" not in result.goals


def test_state_save_failure_is_captured(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
    monkeypatch,
) -> None:
    def _boom(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("save exploded")

    monkeypatch.setattr(stages_module, "save_state", _boom)
    result = asyncio.run(build_context(_request(sample_items), dummy_config))

    assert any(err.stage == "save_state" for err in result.errors)
    statuses = {c.name: c.status for c in result.component_statuses}
    assert statuses["save_state"] == "error"


class _NonJsonLLM:
    provider = "bedrock"

    async def complete(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return "not-json"


def test_analyzer_non_json_response_falls_back(
    sample_items: list[ExtractedItem],
) -> None:
    context_items = [
        build_module.ContextItem.from_extracted(item, "proj") for item in sample_items
    ]
    result = asyncio.run(
        analyze_corpus(
            items=context_items,
            goal_prompt="Understand architecture",
            llm=_NonJsonLLM(),  # type: ignore[arg-type]
            model="model",
            provider="bedrock",
        )
    )
    assert result.goals
    assert "Understand architecture" not in result.goals
    assert result.intent_alignment["user_prompt"] == "Understand architecture"


def test_context_renderer_contains_required_sections_on_sparse_analysis(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
) -> None:
    context_items = [
        build_module.ContextItem.from_extracted(item, "proj") for item in sample_items
    ]
    path = dummy_config.resolve_context_md_path("proj", version="v1")
    result = asyncio.run(
        generate_context_md(
            project="proj",
            items=context_items,
            analysis=build_module.AnalysisResult(),
            context_md_path=path,
        )
    )
    assert result["status"] == "ok"
    body = path.read_text(encoding="utf-8")
    assert "What was given (direct evidence)" in body
    assert "No evidence found in current corpus." in body
