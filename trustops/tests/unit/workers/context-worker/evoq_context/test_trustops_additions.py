"""TrustOps additions to the cloned engine (UPSTREAM.md): the synthesis off
switch, the Index Contract FCRM keys, and ``filters`` on search()."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest
from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.errors import MetadataContractViolation
from evoq_context.core.models import ExtractedItem
from evoq_context.llm.client import LLMClient
from evoq_context.pipeline.build import build_context
from evoq_context.retrieval.context_engine import get_deep_context, search
from evoq_context.vector.contract import (
    CHUNK_STRATEGIES,
    FILTERABLE_KEYS,
    SOURCE_TYPES,
    content_hash_of,
    validate_filter_keys,
    validate_metadata,
    vector_key,
)
from evoq_context.vector.memory_store import InMemoryVectorStore
from fcrm_policy.search.filters import ALLOWED_METADATA_KEYS


class _ChatSpy:
    """Stands in for ``LLMClient.complete``; fails the test if ever awaited."""

    def __init__(self, *, fail: bool) -> None:
        self.calls = 0
        self.fail = fail

    async def __call__(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        if self.fail:
            raise AssertionError("LLMClient.complete must not run with synthesis off")
        return "{}"


def _bedrock(config: ContextConfig, *, synthesis: bool) -> ContextConfig:
    """A real (non-dummy) chat provider, so only the switch can keep it silent."""
    return dataclasses.replace(
        config,
        synthesis_enabled=synthesis,
        analysis_provider="bedrock",
        synthesis_provider="bedrock",
    )


def _request(items: list[ExtractedItem]) -> ContextBuildRequest:
    return ContextBuildRequest(
        project="proj", goal_prompt="Goals", extracted_items=items
    )


async def test_build_context_synthesis_off_never_calls_chat_model(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _ChatSpy(fail=True)
    monkeypatch.setattr(LLMClient, "complete", spy)
    config = _bedrock(dummy_config, synthesis=False)

    result = await build_context(_request(sample_items), config)

    assert spy.calls == 0
    assert result.status == "success"
    statuses = {c.name: c.status for c in result.component_statuses}
    assert statuses["analysis"] == "skipped"
    assert statuses["render"] == "skipped"
    assert statuses["vector_write"] == "success"
    assert not config.resolve_context_md_path("proj", version="v1").exists()
    assert result.goals  # fallback goals, no LLM


async def test_build_context_synthesis_off_rebuild_is_noop(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No context.md is not a failed render to self-repair when synthesis is off."""
    monkeypatch.setattr(LLMClient, "complete", _ChatSpy(fail=True))
    config = _bedrock(dummy_config, synthesis=False)
    await build_context(_request(sample_items), config)
    again = await build_context(_request(sample_items), config)
    assert again.version == "v1"
    assert {c.name: c.status for c in again.component_statuses}[
        "vector_write"
    ] == "skipped"


async def test_build_context_synthesis_on_does_call_chat_model(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Positive control: the spy is wired to the real call path."""
    spy = _ChatSpy(fail=False)
    monkeypatch.setattr(LLMClient, "complete", spy)
    config = _bedrock(dummy_config, synthesis=True)
    await build_context(_request(sample_items), config)
    assert spy.calls > 0


def test_synthesis_enabled_defaults_off(tmp_path: Any) -> None:
    config = ContextConfig(
        projects_root=tmp_path,
        vector_backend="memory",
        s3v_bucket="",
        s3v_region="us-east-1",
        s3v_index_prefix="context",
    )
    assert config.synthesis_enabled is False


def test_filterable_keys_cover_policy_search_allow_list() -> None:
    assert ALLOWED_METADATA_KEYS <= FILTERABLE_KEYS
    assert "policy" in SOURCE_TYPES
    assert "clause_units" in CHUNK_STRATEGIES


def _clause_meta(**extra: Any) -> dict[str, Any]:
    doc_id = "policy:v14FCRM4231"
    content_hash = content_hash_of(b"clause text")
    return {
        "doc_id": doc_id,
        "project": "fcrm",
        "source_type": "policy",
        "source_id": "INT-FCRM-4.2",
        "content_hash": content_hash,
        "chunk_key": vector_key(doc_id, content_hash, 0),
        "chunk_index": 0,
        "offset": 0,
        "chunk_strategy": "clause_units",
        "type": "markdown",
        "snapshot_version": "v1",
        "index_contract": 2,
        "text_truncated": False,
        "text": "clause text",
        "corpus_version": "v14",
        "trigger_type": "new_geography",
        "risk_area": "geography",
        "jurisdiction": "MX",
        "closed_date": 20600,
        "clause_id": "FCRM42-3.1",
        "index_kind": "clause",
        **extra,
    }


def test_validate_metadata_accepts_fcrm_keys() -> None:
    validate_metadata(_clause_meta())


def test_validate_metadata_accepts_the_precedent_and_control_record_kinds() -> None:
    precedent_id = "precedent:FCRM20260072"
    precedent_hash = content_hash_of(b"precedent text")
    validate_metadata(
        _clause_meta(
            doc_id=precedent_id,
            source_type="precedent",
            source_id="FCRM-2026-0072",
            content_hash=precedent_hash,
            chunk_key=vector_key(precedent_id, precedent_hash, 0),
            type="plain_text",
            index_kind="precedent",
            case_id="FCRM-2026-0072",
            outcome="approved",
            band=2,
        )
    )
    control_id = "control:CTLT01"
    control_hash = content_hash_of(b"control text")
    validate_metadata(
        _clause_meta(
            doc_id=control_id,
            source_type="control",
            source_id="CTL-T01",
            content_hash=control_hash,
            chunk_key=vector_key(control_id, control_hash, 0),
            type="plain_text",
            index_kind="control",
            control_id="CTL-T01",
            controls_version="v9",
        )
    )


def test_the_new_identifier_keys_are_filterable() -> None:
    validate_filter_keys(
        {"$and": [{"case_id": "FCRM-2026-0072"}, {"controls_version": "v9"}]}
    )
    validate_filter_keys({"control_id": "CTL-T01", "outcome": "approved", "band": 2})


def test_validate_metadata_rejects_unknown_key() -> None:
    with pytest.raises(MetadataContractViolation, match="risk_level"):
        validate_metadata(_clause_meta(risk_level="high"))


def test_validate_filter_keys_rejects_unknown_and_walks_logical_ops() -> None:
    validate_filter_keys(
        {"$and": [{"risk_area": "channel"}, {"corpus_version": "v14"}]}
    )
    with pytest.raises(MetadataContractViolation):
        validate_filter_keys({"$or": [{"source_id": "OFAC"}, {"riskarea": "x"}]})


async def _store_with_two_sources(config: ContextConfig) -> InMemoryVectorStore:
    from evoq_context.vector.interface import VectorChunk

    store = InMemoryVectorStore(config, "fcrm")
    chunks = []
    for idx, source in enumerate(("FFIEC", "OFAC")):
        meta = _clause_meta(
            source_id=source, chunk_key=f"policy:v14FCRM4231:{idx:016x}:0000"
        )
        chunks.append(
            VectorChunk(
                chunk_id=meta["chunk_key"],
                text="t",
                embedding=[1.0, 0.0],
                metadata=meta,
            )
        )
    await store.upsert(chunks)
    return store


async def test_get_deep_context_filters_narrow_and_keep_project_scope(
    dummy_config: ContextConfig,
) -> None:
    store = await _store_with_two_sources(dummy_config)
    both = await get_deep_context("fcrm", [1.0, 0.0], store, top_k=10)
    ffiec = await get_deep_context(
        "fcrm", [1.0, 0.0], store, 10, filters={"source_id": "FFIEC"}
    )
    ofac = await get_deep_context(
        "fcrm", [1.0, 0.0], store, 10, filters={"source_id": "OFAC"}
    )
    widen = await get_deep_context(
        "other", [1.0, 0.0], store, 10, filters={"project": "fcrm"}
    )
    assert len(both) == 2
    assert [r.metadata["source_id"] for r in ffiec] == ["FFIEC"]
    assert [r.metadata["source_id"] for r in ofac] == ["OFAC"]
    assert widen == []  # the project scope always wins over caller filters


async def test_search_rejects_unknown_filter_key_before_embedding(
    dummy_config: ContextConfig,
) -> None:
    store = InMemoryVectorStore(dummy_config, "fcrm")
    llm = LLMClient.from_config(dummy_config)
    with pytest.raises(MetadataContractViolation):
        await search("fcrm", "q", dummy_config, llm, store, filters={"severity": "3"})
