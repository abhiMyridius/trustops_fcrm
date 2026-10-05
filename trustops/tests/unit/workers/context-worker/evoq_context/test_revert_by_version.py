from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.models import ExtractedItem
from evoq_context.pipeline.build import build_context
from evoq_context.pipeline.revert import revert_context_to
from evoq_context.pipeline.vectors import VectorWriter


def write_state(path: Path, doc_hash_index: dict[str, str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "project": "proj",
        "run_id": "run",
        "state_version_key": "sv",
        "build_version_key": "bv",
        "documents_total": len(doc_hash_index or {}),
        "doc_hash_index": doc_hash_index or {},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_normalize_version_label_accepts_numeric_and_label() -> None:
    assert ContextConfig.normalize_version_label(3) == "v3"
    assert ContextConfig.normalize_version_label("3") == "v3"
    assert ContextConfig.normalize_version_label("v3") == "v3"
    assert ContextConfig.normalize_version_label(" V3 ") == "v3"
    assert ContextConfig.version_label_to_number("v12") == 12


@pytest.mark.parametrize("value", ["", "abc", "v0", 0, -1, "v-2"])
def test_normalize_version_label_rejects_invalid(value: str | int) -> None:
    with pytest.raises(ValueError):
        ContextConfig.normalize_version_label(value)


def test_revert_context_to_missing_target_is_strict_error_and_pointer_unchanged(
    tmp_path: Path,
    dummy_config: ContextConfig,
) -> None:
    project = "proj"
    config = dummy_config
    current_pointer = config.current_pointer_path(project)
    current_pointer.parent.mkdir(parents=True, exist_ok=True)
    current_pointer.write_text("v2\n", encoding="utf-8")
    write_state(config.state_path(project, version="v2"), {"a": "h1"})

    result = asyncio.run(revert_context_to(project, config, 3))

    assert result.status == "error"
    assert result.from_version == 2
    assert result.to_version == 3
    assert "has no state.json" in result.message
    assert current_pointer.read_text(encoding="utf-8").strip() == "v2"


def test_revert_context_to_accepts_numeric_and_switches_pointer(
    dummy_config: ContextConfig,
) -> None:
    project = "proj"
    config = dummy_config
    current_pointer = config.current_pointer_path(project)
    current_pointer.parent.mkdir(parents=True, exist_ok=True)
    current_pointer.write_text("v2\n", encoding="utf-8")
    write_state(config.state_path(project, version="v2"), {"a": "h1", "b": "h2"})
    write_state(config.state_path(project, version="v1"), {"a": "h1"})
    config.items_dir(project, version="v1").mkdir(parents=True, exist_ok=True)

    result = asyncio.run(revert_context_to(project, config, 1))

    assert result.status == "success"
    assert result.from_version == 2
    assert result.to_version == 1
    assert current_pointer.read_text(encoding="utf-8").strip() == "v1"


def test_revert_context_to_noop_when_same_target(
    dummy_config: ContextConfig,
) -> None:
    project = "proj"
    config = dummy_config
    current_pointer = config.current_pointer_path(project)
    current_pointer.parent.mkdir(parents=True, exist_ok=True)
    current_pointer.write_text("v2\n", encoding="utf-8")
    write_state(config.state_path(project, version="v2"), {"a": "h1"})

    result = asyncio.run(revert_context_to(project, config, "2"))

    assert result.status == "success"
    assert result.from_version == 2
    assert result.to_version == 2
    assert "Already at snapshot" in result.message


# ---------------------------------------------------------------------------
# Stage-based revert (plan §2.5): deterministic double-revert, and a
# partial re-embed failure that fails the whole revert (unlike a normal
# build, which tolerates partial embedding failure).
# ---------------------------------------------------------------------------


def _items() -> list[ExtractedItem]:
    return [
        ExtractedItem(
            source_type="file",
            source_id="F1",
            title="Doc One",
            body="Original body one.",
        ),
        ExtractedItem(
            source_type="file",
            source_id="F2",
            title="Doc Two",
            body="Original body two.",
        ),
    ]


def _build_two_versions(config: ContextConfig) -> None:
    """v1: two docs. v2: F1's body changes (content-hash differs)."""
    first = ContextBuildRequest(
        project="proj", goal_prompt="goal", extracted_items=_items()
    )
    asyncio.run(build_context(first, config))

    changed = _items()
    changed[0] = ExtractedItem(
        source_type="file",
        source_id="F1",
        title="Doc One",
        body="Updated body one — different content.",
    )
    second = ContextBuildRequest(
        project="proj", goal_prompt="goal", extracted_items=changed
    )
    asyncio.run(build_context(second, config))


def test_double_revert_is_deterministic(dummy_config: ContextConfig) -> None:
    """Reverting v2 -> v1 twice in a row produces byte-identical
    vector_manifest.json/index_manifest.json and the same pointer state —
    the fixed stage ordering + sorted() doc-id iteration make this so."""
    config = dummy_config
    _build_two_versions(config)
    assert config.read_current_version_label("proj") == "v2"

    first = asyncio.run(revert_context_to("proj", config, 1))
    assert first.status == "success"
    assert config.read_current_version_label("proj") == "v1"
    vm_path = config.vector_manifest_path("proj", version="v1")
    im_path = config.index_manifest_path("proj", version="v1")
    vm_after_first = vm_path.read_text(encoding="utf-8")
    im_after_first = json.loads(im_path.read_text(encoding="utf-8"))
    im_after_first.pop("generated_at", None)

    # Revert back to v2, then to v1 again — a second v2->v1 revert.
    back_to_two = asyncio.run(revert_context_to("proj", config, 2))
    assert back_to_two.status == "success"
    second = asyncio.run(revert_context_to("proj", config, 1))
    assert second.status == "success"

    assert config.read_current_version_label("proj") == "v1"
    assert vm_path.read_text(encoding="utf-8") == vm_after_first
    im_after_second = json.loads(im_path.read_text(encoding="utf-8"))
    im_after_second.pop("generated_at", None)
    assert im_after_second == im_after_first
    assert first.vectors_deleted == second.vectors_deleted
    assert first.vectors_upserted == second.vectors_upserted


def test_partial_reembed_failure_fails_whole_revert_and_pointer_unchanged(
    dummy_config: ContextConfig,
    monkeypatch,
) -> None:
    config = dummy_config
    _build_two_versions(config)
    assert config.read_current_version_label("proj") == "v2"

    async def _boom(*args, **kwargs):
        raise RuntimeError("simulated re-embed failure")

    monkeypatch.setattr(VectorWriter, "write", _boom)

    result = asyncio.run(revert_context_to("proj", config, 1))

    assert result.status == "error"
    assert result.errors
    # A partial/total re-embed failure never reports a normal build-style
    # "partial success" — the whole revert fails, unlike a build which
    # tolerates and retries individual document failures.
    assert any(
        e.code in ("REVERT_PARTIAL_REEMBED", "REVERT_REEMBED_ERROR")
        for e in result.errors
    )
    # The pointer must still be at the pre-revert version.
    assert config.read_current_version_label("proj") == "v2"


def test_partial_reembed_document_failure_fails_whole_revert(
    dummy_config: ContextConfig,
    monkeypatch,
) -> None:
    """A VectorWriteReport with some (not all) documents failed still
    fails the whole revert — this is the "partial" case the module
    docstring calls out as deliberately less forgiving than a normal build."""
    config = dummy_config
    _build_two_versions(config)

    real_write = VectorWriter.write

    async def _partial_failure(self, items, **kwargs):
        report = await real_write(self, items, **kwargs)
        if not report.failed:
            from evoq_context.pipeline.vectors import DocVectorFailure

            report.failed.append(
                DocVectorFailure(
                    doc_id="file:F1", code="CHUNK_ERROR", error="synthetic"
                )
            )
        return report

    monkeypatch.setattr(VectorWriter, "write", _partial_failure)

    result = asyncio.run(revert_context_to("proj", config, 1))

    assert result.status == "error"
    assert any(e.code == "REVERT_PARTIAL_REEMBED" for e in result.errors)
    assert config.read_current_version_label("proj") == "v2"
