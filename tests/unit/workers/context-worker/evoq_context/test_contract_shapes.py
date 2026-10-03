from __future__ import annotations

import asyncio

from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.models import ExtractedItem
from evoq_context.pipeline.build import build_context
from evoq_context.pipeline.revert import revert_context_to


def test_context_build_result_to_dict_shape(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
) -> None:
    req = ContextBuildRequest(
        project="proj",
        goal_prompt="Goals",
        extracted_items=sample_items,
    )
    result = asyncio.run(build_context(req, dummy_config))
    payload = result.to_dict()

    assert payload["project"] == "proj"
    assert isinstance(payload["status"], str)
    assert isinstance(payload["message"], str)
    assert isinstance(payload["goals"], list)
    assert isinstance(payload["stats"], dict)
    assert isinstance(payload["errors"], list)
    assert isinstance(payload["component_statuses"], list)
    assert isinstance(payload["context_location"], dict)
    assert isinstance(payload["analysis"], dict)
    assert isinstance(payload["version"], str)


def test_revert_result_to_dict_shape_on_error(
    dummy_config: ContextConfig,
) -> None:
    result = asyncio.run(revert_context_to("proj", dummy_config, 9))
    payload = result.to_dict()

    assert payload["status"] == "error"
    assert isinstance(payload["message"], str)
    assert isinstance(payload["errors"], list)
    from_version = payload.get("from_version")
    to_version = payload.get("to_version")
    assert from_version is None or isinstance(from_version, int)
    assert to_version is None or isinstance(to_version, int)
