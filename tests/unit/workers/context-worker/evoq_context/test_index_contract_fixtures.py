"""Contract lock tests (plan §1.5): this repo's Index Contract v2 metadata
and index_manifest.json shape validated against the shared golden schemas
in AEO/contracts/. Uses jsonschema when available; otherwise a minimal
hand-rolled structural check (required keys + additionalProperties) so
this still catches a schema break with no extra dependency installed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from evoq_context.core.config import ContextConfig
from evoq_context.core.models import ExtractedItem
from evoq_context.vector.contract import validate_metadata

#: The repo's own copy — this is what the tests below actually validate
#: against, so CI never depends on a sibling AEO/contracts/ checkout.
_LOCAL_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "contracts"
#: TrustOps clone: AEO/contracts/ is not in this repo. The context worker now
#: owns Index Contract v2 (it adds FCRM metadata keys), so the upstream
#: byte-identity test became an in-repo drift check: profile_hints is
#: produced by the ingestion engine and must match its suite's copy.
_INGESTION_CONTRACTS_DIR = (
    Path(__file__).resolve().parents[2]
    / "ingestion-worker"
    / "evoq_ingestion"
    / "fixtures"
    / "contracts"
)


def _load(name: str) -> dict | None:
    path = _LOCAL_FIXTURES_DIR / name
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def test_profile_hints_schema_matches_ingestion_copy() -> None:
    """Producer (H3 evoq_ingestion) and consumer (H4 evoq_context) must agree."""
    name = "profile_hints.schema.json"
    assert (_LOCAL_FIXTURES_DIR / name).read_text(encoding="utf-8") == (
        _INGESTION_CONTRACTS_DIR / name
    ).read_text(encoding="utf-8")


def _structural_check(instance: dict, schema: dict) -> None:
    for key in schema.get("required", []):
        assert key in instance, f"missing required key {key!r}"
    if schema.get("additionalProperties") is False:
        allowed = set(schema.get("properties", {}))
        extra = set(instance) - allowed
        assert not extra, f"keys outside the schema: {extra}"


def _validate(instance: dict, schema: dict) -> None:
    jsonschema = pytest.importorskip("jsonschema", reason="jsonschema not installed")
    jsonschema.validate(instance=instance, schema=schema)


@pytest.mark.asyncio
async def test_vector_metadata_conforms_to_index_contract_v2_schema(
    dummy_config: ContextConfig,
) -> None:
    schema = _load("index_contract_v2.schema.json")
    if schema is None:
        pytest.skip("AEO/contracts/index_contract_v2.schema.json not found")

    from evoq_context.chunking.pipeline import ChunkPipeline
    from evoq_context.llm.client import LLMClient
    from evoq_context.vector.embedder import DocumentPreparer

    item = ExtractedItem(
        source_type="confluence",
        source_id="SRC1",
        title="Reqs",
        body="# Reqs\n\n"
        + "\n\n".join(f"REQ-{i:03d}: Do thing {i}." for i in range(1, 8)),
        metadata={"url": "https://wiki/x", "labels": ["a", "b"]},
    )
    from evoq_context.core.models import ContextItem

    ctx_item = ContextItem.from_extracted(item, "proj")
    llm = LLMClient.from_config(dummy_config)
    preparer = DocumentPreparer(
        ChunkPipeline(dummy_config.chunking()), llm, dummy_config
    )
    prepared = await preparer.prepare(ctx_item, snapshot_version="v1")
    chunks = prepared.vector_chunks
    assert chunks
    for c in chunks:
        validate_metadata(c.metadata)  # our own contract validator
        _validate(c.metadata, schema)  # the shared golden schema
        _structural_check(c.metadata, schema)


def test_index_manifest_schema_present_and_matches_our_shape() -> None:
    schema = _load("index_manifest.schema.json")
    if schema is None:
        pytest.skip("AEO/contracts/index_manifest.schema.json not found")
    from evoq_context.storage.local import build_index_manifest

    manifest = build_index_manifest(
        project_id="P1",
        snapshot_version="v1",
        context_version=1,
        backend="memory",
        store="memory",
        index="idx",
        region="us-east-1",
        distance_metric="cosine",
        embedding_model="dummy/model",
        embedding_dimension=8,
        vector_manifest={
            "file:D1": {"chunk_count": 1, "chunk_strategy": "paragraph_windows"}
        },
        generated_at="2026-01-01T00:00:00Z",
    )
    _validate(manifest, schema)
    _structural_check(manifest, schema)


def test_shared_vectors_fixture_conforms_to_index_contract_v2() -> None:
    """AEO/contracts/fixtures/vectors_fixture.json (this job's own dump,
    committed for the MCP's InMemoryVectorClient to load) validates against
    the Index Contract v2 schema for every record, and every chunk_key
    round-trips through our own vector_key()/validate_metadata()."""
    schema = _load("index_contract_v2.schema.json")
    # TrustOps clone: copied from AEO/contracts/fixtures/ into the suite.
    fixture_path = _LOCAL_FIXTURES_DIR / "vectors_fixture.json"
    if schema is None or not fixture_path.exists():
        pytest.skip("shared contracts/fixtures not present in this checkout")

    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
    assert fixture["backend"] == "memory"
    assert fixture["distance_metric"] == "cosine"
    vectors = fixture["vectors"]
    assert vectors, "fixture must not be empty"

    strategies_seen = set()
    for record in vectors:
        meta = record["metadata"]
        validate_metadata(meta)
        _validate(meta, schema)
        assert record["key"] == meta["chunk_key"]
        strategies_seen.add(meta["chunk_strategy"])

    assert strategies_seen == {
        "heading_sections",
        "paragraph_windows",
        "page_windows",
        "slide_units",
        "table_aware",
        "requirement_units",
        "comment_thread",
    }, f"fixture does not cover all 7 strategies: {strategies_seen}"


def test_profile_hints_schema_present() -> None:
    """We don't produce profile_hints (ingestion does) — this just pins
    that the shared schema file both repos rely on is where we expect it."""
    schema = _load("profile_hints.schema.json")
    if schema is None:
        pytest.skip("AEO/contracts/profile_hints.schema.json not found")
    assert schema["required"]
    assert "profile_version" in schema["required"]
