"""index_manifest.json shape (AEO/contracts/index_manifest.schema.json) and
the "fenced block in context.md == the standalone file" invariant."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest
from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.models import ExtractedItem
from evoq_context.pipeline.build import build_context
from evoq_context.storage.local import build_index_manifest, read_index_manifest

_SCHEMA_PATH = (
    # TrustOps clone: the suite's own fixture copy (AEO/contracts/ is not in this repo)
    Path(__file__).resolve().parent
    / "fixtures"
    / "contracts"
    / "index_manifest.schema.json"
)


def _load_schema() -> dict | None:
    if _SCHEMA_PATH.exists():
        return json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    return None


def _assert_matches_schema(manifest: dict, schema: dict) -> None:
    for key in schema["required"]:
        assert key in manifest, f"index_manifest.json missing required key {key!r}"
    allowed = set(schema["properties"])
    assert set(manifest) <= allowed, f"unexpected keys: {set(manifest) - allowed}"
    assert manifest["manifest_version"] == 1
    assert manifest["index_contract"] == 2
    assert re.match(r"^v[0-9]+$", manifest["snapshot_version"])
    assert (
        manifest["distance_metric"] in schema["properties"]["distance_metric"]["enum"]
    )


def test_build_index_manifest_matches_schema_when_present() -> None:
    manifest = build_index_manifest(
        project_id="PRJ1",
        snapshot_version="v3",
        context_version=3,
        backend="memory",
        store="memory",
        index="context-prj1",
        region="us-east-1",
        distance_metric="cosine",
        embedding_model="dummy/amazon.titan-embed-text-v2:0",
        embedding_dimension=8,
        vector_manifest={
            "file:D1": {"chunk_count": 5, "chunk_strategy": "paragraph_windows"},
            "file:D2": {"chunk_count": 3, "chunk_strategy": "heading_sections"},
        },
        generated_at="2026-01-01T00:00:00Z",
    )
    schema = _load_schema()
    if schema is None:
        pytest.skip("AEO/contracts/index_manifest.schema.json not found")
    _assert_matches_schema(manifest, schema)
    assert manifest["chunk_count"] == 8
    assert manifest["doc_count"] == 2
    assert manifest["strategy_histogram"] == {
        "paragraph_windows": 5,
        "heading_sections": 3,
    }


def test_build_index_manifest_empty_manifest() -> None:
    manifest = build_index_manifest(
        project_id="PRJ1",
        snapshot_version="v1",
        context_version=1,
        backend="memory",
        store="memory",
        index="idx",
        region="",
        distance_metric="cosine",
        embedding_model="dummy/model",
        embedding_dimension=8,
        vector_manifest={},
        generated_at="2026-01-01T00:00:00Z",
    )
    assert manifest["chunk_count"] == 0
    assert manifest["doc_count"] == 0
    assert manifest["strategy_histogram"] == {}


def test_fenced_index_manifest_block_equals_the_standalone_file(
    dummy_config: ContextConfig,
) -> None:
    items = [
        ExtractedItem(
            source_type="file",
            source_id="F1",
            title="Doc",
            body="Some text about the system.",
        )
    ]
    req = ContextBuildRequest(project="proj", goal_prompt="goal", extracted_items=items)
    result = asyncio.run(build_context(req, dummy_config))
    assert result.status == "success"

    manifest_file = read_index_manifest(
        dummy_config.index_manifest_path("proj", version=result.version)
    )
    assert manifest_file is not None

    context_md = Path(result.context_location["context_md"]).read_text(encoding="utf-8")
    match = re.search(
        r"## Index manifest\s*\n+```json\n(.*?)\n```", context_md, re.DOTALL
    )
    assert match, (
        "context.md must carry exactly one fenced ## Index manifest json block"
    )
    fenced_manifest = json.loads(match.group(1))

    assert fenced_manifest == manifest_file


def test_index_manifest_absent_when_no_vector_backend(tmp_path) -> None:
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
        synthesis_enabled=True,  # TrustOps: asserts on context.md, so synthesis on
    )
    items = [
        ExtractedItem(source_type="file", source_id="F1", title="Doc", body="text")
    ]
    req = ContextBuildRequest(project="proj", goal_prompt="goal", extracted_items=items)
    result = asyncio.run(build_context(req, config))
    assert result.status == "success"

    manifest_file = read_index_manifest(
        config.index_manifest_path("proj", version=result.version)
    )
    assert manifest_file is None

    context_md = Path(result.context_location["context_md"]).read_text(encoding="utf-8")
    assert "## Index manifest" not in context_md
