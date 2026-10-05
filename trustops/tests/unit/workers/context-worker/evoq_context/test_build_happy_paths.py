from __future__ import annotations

import asyncio
from pathlib import Path

from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.models import ExtractedItem
from evoq_context.pipeline.build import build_context
from evoq_context.pipeline.revert import revert_context, revert_context_to
from evoq_context.retrieval.context_engine import get_context


def test_build_happy_path_creates_v1_and_artifacts(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
) -> None:
    request = ContextBuildRequest(
        project="proj",
        goal_prompt="Generate goals",
        extracted_items=sample_items,
    )

    result = asyncio.run(build_context(request, dummy_config))

    assert result.status == "success"
    assert result.version == "v1"
    assert result.stats["documents_received"] == 2
    assert result.stats["new"] == 2
    assert result.change_details["new_ids"]
    assert dummy_config.state_path("proj", version="v1").exists()
    assert dummy_config.resolve_context_md_path("proj", version="v1").exists()
    assert dummy_config.resolve_document_inventory_path("proj", version="v1").exists()


def test_build_no_change_reuses_snapshot_version(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
) -> None:
    request = ContextBuildRequest(
        project="proj",
        goal_prompt="Generate goals",
        extracted_items=sample_items,
    )
    first = asyncio.run(build_context(request, dummy_config))
    second = asyncio.run(build_context(request, dummy_config))

    assert first.version == "v1"
    assert second.status == "success"
    assert second.version == "v1"
    assert second.stats["unchanged"] == 2
    assert second.stats["new"] == 0


def test_build_no_change_prompt_change_refreshes_context(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
) -> None:
    first = ContextBuildRequest(
        project="proj",
        goal_prompt="Understand project architecture",
        extracted_items=sample_items,
    )
    second = ContextBuildRequest(
        project="proj",
        goal_prompt="Focus on release readiness risks",
        extracted_items=sample_items,
    )

    first_result = asyncio.run(build_context(first, dummy_config))
    second_result = asyncio.run(build_context(second, dummy_config))

    assert first_result.version == "v1"
    assert second_result.version == "v1"
    assert second_result.status == "success"
    assert second_result.stats["new"] == 0
    assert second_result.stats["updated"] == 0
    assert "Focus on release readiness risks" not in second_result.goals
    assert second_result.analysis["intent_alignment"]["user_prompt"] == (
        "Focus on release readiness risks"
    )
    context_path = Path(second_result.context_location["context_md"])
    context_body = context_path.read_text(encoding="utf-8")
    assert "Project context v2" in context_body
    assert "Contextual goals" in context_body
    assert "What was given (direct evidence)" in context_body
    assert "What was inferred" in context_body


def test_revert_flow_previous_and_explicit_target(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
) -> None:
    initial = ContextBuildRequest(
        project="proj",
        goal_prompt="Generate goals",
        extracted_items=sample_items,
    )
    asyncio.run(build_context(initial, dummy_config))

    changed_items = list(sample_items)
    changed_items[0] = ExtractedItem(
        source_type=changed_items[0].source_type,
        source_id=changed_items[0].source_id,
        title=changed_items[0].title,
        body="Updated body",
    )
    changed = ContextBuildRequest(
        project="proj",
        goal_prompt="Generate goals",
        extracted_items=changed_items,
    )
    asyncio.run(build_context(changed, dummy_config))

    previous = asyncio.run(revert_context("proj", dummy_config))
    explicit = asyncio.run(revert_context_to("proj", dummy_config, "2"))

    assert previous.status == "success"
    assert previous.from_version == 2
    assert previous.to_version == 1
    assert explicit.status == "success"
    assert explicit.to_version == 2


class _BrokenVectorStore:
    async def query(self, *args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("vector unavailable")


def test_retrieval_high_level_still_returns_when_deep_query_fails(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
) -> None:
    req = ContextBuildRequest(
        project="proj",
        goal_prompt="Generate goals",
        extracted_items=sample_items,
    )
    asyncio.run(build_context(req, dummy_config))
    result = asyncio.run(
        get_context(
            project="proj",
            config=dummy_config,
            vector_store=_BrokenVectorStore(),
            query_embedding=[0.1, 0.2, 0.3],
        )
    )

    assert "# proj — Project context v2" in result.high_level
    assert result.retrieved_chunks == []
