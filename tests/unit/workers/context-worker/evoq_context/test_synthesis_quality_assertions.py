from __future__ import annotations

import asyncio
from pathlib import Path

from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.models import ExtractedItem
from evoq_context.pipeline.build import build_context


def test_context_v2_contains_given_found_inferred_sections(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
) -> None:
    request = ContextBuildRequest(
        project="proj",
        goal_prompt="Understand the current implementation scope",
        extracted_items=sample_items,
    )
    result = asyncio.run(build_context(request, dummy_config))
    context_path = Path(result.context_location["context_md"])
    body = context_path.read_text(encoding="utf-8")
    assert "Contextual goals" in body
    assert "What was given (direct evidence)" in body
    assert "What was found (synthesized findings)" in body
    assert "What was inferred" in body


def test_goals_do_not_echo_user_prompt_verbatim(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
) -> None:
    user_prompt = "Understand the current implementation scope"
    request = ContextBuildRequest(
        project="proj",
        goal_prompt=user_prompt,
        extracted_items=sample_items,
    )
    result = asyncio.run(build_context(request, dummy_config))
    goals = result.goals
    assert goals
    assert all(goal != user_prompt for goal in goals)
    assert result.analysis["intent_alignment"]["user_prompt"] == user_prompt


def test_inference_rows_include_confidence_and_source_references(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
) -> None:
    request = ContextBuildRequest(
        project="proj",
        goal_prompt="Identify likely delivery risks",
        extracted_items=sample_items,
    )
    result = asyncio.run(build_context(request, dummy_config))
    inferences = result.analysis.get("inferences", [])
    for inference in inferences:
        if not isinstance(inference, dict):
            continue
        assert "confidence" in inference
        assert "source_doc_ids" in inference
