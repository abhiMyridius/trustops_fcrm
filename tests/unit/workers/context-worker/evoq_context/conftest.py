from __future__ import annotations

import json
from pathlib import Path

import pytest
from evoq_context.core.config import ContextConfig
from evoq_context.core.models import ExtractedItem


@pytest.fixture
def dummy_config(tmp_path: Path) -> ContextConfig:
    return ContextConfig(
        projects_root=tmp_path,
        vector_backend="memory",
        s3v_bucket="",
        s3v_region="us-east-1",
        s3v_index_prefix="context",
        embedding_model="amazon.titan-embed-text-v2:0",
        embedding_provider="dummy",
        embedding_dimension=8,
        embedding_max_input_tokens=50_000,
        embedding_input_safety=1.0,
        embedding_batch_size=16,
        embedding_max_retries=4,
        chunk_tokenizer="tiktoken",
        chunk_target_tokens=700,
        chunk_overlap_tokens=80,
        chunk_min_tokens=120,
        chunk_hard_max_tokens=50_000,
        chunk_max_rows=500,
        llm_max_tokens=15000,
        analysis_model="dummy",
        analysis_provider="dummy",
        synthesis_model="dummy",
        synthesis_provider="dummy",
        # TrustOps: the engine default is now synthesis off; upstream's suite
        # exercises the synthesis path, so its shared fixture opts back in.
        synthesis_enabled=True,
    )


@pytest.fixture
def sample_items() -> list[ExtractedItem]:
    return [
        ExtractedItem(
            source_type="jira",
            source_id="SRC1",
            title="Issue one",
            body="First issue body",
        ),
        ExtractedItem(
            source_type="confluence",
            source_id="SRC2",
            title="Page two",
            body="Second page body",
        ),
    ]


def write_state(
    path: Path,
    doc_hash_index: dict[str, str] | None = None,
    goal_prompt_hash: str = "",
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "project": "proj",
        "run_id": "run",
        "state_version_key": "sv",
        "build_version_key": "bv",
        "documents_total": len(doc_hash_index or {}),
        "doc_hash_index": doc_hash_index or {},
    }
    if goal_prompt_hash:
        payload["goal_prompt_hash"] = goal_prompt_hash
    path.write_text(json.dumps(payload), encoding="utf-8")
