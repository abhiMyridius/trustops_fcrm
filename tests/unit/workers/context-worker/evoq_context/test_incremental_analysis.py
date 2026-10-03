"""Tests for the incremental per-document corpus analysis."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import evoq_context.pipeline.build as build_module
import pytest
from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.models import ExtractedItem
from evoq_context.pipeline.build import build_context
from evoq_context.synthesis._util import extract_json_from_response
from evoq_context.synthesis.analyzer import (
    AnalysisResult,
    DocExtraction,
    _analysis_from_extractions,
    _extract_all_documents,
    _extract_single_document,
    _synthesize_accumulated,
    analyze_corpus,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_context_item(
    doc_id: str = "doc:SRC1",
    title: str = "Test Doc",
    source_type: str = "confluence",
    body: str = "Some document body text.",
) -> build_module.ContextItem:
    item = ExtractedItem(
        source_type=source_type,
        source_id=doc_id,
        title=title,
        body=body,
    )
    return build_module.ContextItem.from_extracted(item, "proj")


def _make_extraction(
    doc_id: str = "doc:SRC1",
    title: str = "Test Doc",
    failed: bool = False,
) -> DocExtraction:
    if failed:
        return DocExtraction(
            doc_id=doc_id,
            title=title,
            source_type="confluence",
            extraction_failed=True,
        )
    return DocExtraction(
        doc_id=doc_id,
        title=title,
        source_type="confluence",
        category="technical",
        summary="This document describes the API architecture.",
        key_concepts=["REST API", "authentication", "JWT"],
        evidence=[
            {
                "statement": "The API uses JWT for authentication.",
                "relevance": "Security model.",
            }
        ],
    )


# ---------------------------------------------------------------------------
# extract_json_from_response tests
# ---------------------------------------------------------------------------


class TestExtractJsonFromResponse:
    def test_clean_json_returned_as_is(self) -> None:
        payload = '{"key": "value"}'
        result = extract_json_from_response(payload)
        data = json.loads(result)
        assert data["key"] == "value"

    def test_fenced_json_block_extracted(self) -> None:
        payload = '```json\n{"key": "value"}\n```'
        result = extract_json_from_response(payload)
        data = json.loads(result)
        assert data["key"] == "value"

    def test_fence_without_language_tag(self) -> None:
        payload = '```\n{"key": "value"}\n```'
        result = extract_json_from_response(payload)
        data = json.loads(result)
        assert data["key"] == "value"

    def test_prose_before_json_stripped(self) -> None:
        payload = 'Here is the analysis:\n{"key": "value"}'
        result = extract_json_from_response(payload)
        data = json.loads(result)
        assert data["key"] == "value"

    def test_prose_after_json_stripped(self) -> None:
        payload = '{"key": "value"}\nLet me know if you need anything else.'
        result = extract_json_from_response(payload)
        data = json.loads(result)
        assert data["key"] == "value"

    def test_prose_before_and_after_json(self) -> None:
        payload = 'Sure! Here you go:\n{"key": "value"}\nHope that helps.'
        result = extract_json_from_response(payload)
        data = json.loads(result)
        assert data["key"] == "value"

    def test_nested_json_object(self) -> None:
        payload = '{"outer": {"inner": [1, 2, 3]}}'
        result = extract_json_from_response(payload)
        data = json.loads(result)
        assert data["outer"]["inner"] == [1, 2, 3]

    def test_empty_string_raises(self) -> None:
        with pytest.raises(ValueError, match="Empty response"):
            extract_json_from_response("")

    def test_no_json_object_raises(self) -> None:
        with pytest.raises(ValueError, match="No JSON object found"):
            extract_json_from_response("This is just plain text with no JSON.")

    def test_fenced_block_with_surrounding_text(self) -> None:
        payload = (
            "I have analysed the document.\n\n"
            "```json\n"
            '{"category": "technical", "summary": "API docs"}\n'
            "```\n\n"
            "Let me know if you need more detail."
        )
        result = extract_json_from_response(payload)
        data = json.loads(result)
        assert data["category"] == "technical"


# ---------------------------------------------------------------------------
# DocExtraction & _extract_single_document tests
# ---------------------------------------------------------------------------


class _ValidExtractionLLM:
    """Mock LLM that returns a valid per-doc extraction JSON."""

    provider = "bedrock"

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        return json.dumps(
            {
                "category": "technical",
                "summary": "A document about the REST API.",
                "key_concepts": ["REST", "API", "auth"],
                "evidence": [
                    {
                        "statement": "The system uses JWT tokens.",
                        "relevance": "Security relevant.",
                    }
                ],
            }
        )


class _FencedExtractionLLM:
    """Mock LLM that wraps its response in markdown fences."""

    provider = "bedrock"

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        payload = json.dumps(
            {
                "category": "requirements",
                "summary": "User requirements document.",
                "key_concepts": ["authentication", "roles"],
                "evidence": [
                    {"statement": "Users must log in.", "relevance": "Auth flow."}
                ],
            }
        )
        return f"```json\n{payload}\n```"


class _NonJsonExtractionLLM:
    """Mock LLM that returns non-JSON (simulates failure)."""

    provider = "bedrock"

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        return "I cannot process this document at this time."


class _ExceptionLLM:
    """Mock LLM that raises an exception."""

    provider = "bedrock"

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        raise RuntimeError("Bedrock connection timeout")


class _EmptyResponseLLM:
    """Mock LLM that returns empty string."""

    provider = "bedrock"

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        return ""


class _WhitespaceOnlyLLM:
    """Mock LLM that returns only whitespace/newlines."""

    provider = "bedrock"

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        return "   \n\n  "


class _NoneResponseLLM:
    """Mock LLM that returns None (e.g. provider bug)."""

    provider = "bedrock"

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        return None  # type: ignore[return-value]


class TestExtractSingleDocument:
    def test_valid_json_response_populated(self) -> None:
        item = _make_context_item()
        result = asyncio.run(
            _extract_single_document(item, "Find APIs", _ValidExtractionLLM())  # type: ignore[arg-type]
        )
        assert not result.extraction_failed
        assert result.category == "technical"
        assert result.summary == "A document about the REST API."
        assert "REST" in result.key_concepts
        assert len(result.evidence) == 1

    def test_fenced_json_response_handled(self) -> None:
        item = _make_context_item()
        result = asyncio.run(
            _extract_single_document(item, "Find APIs", _FencedExtractionLLM())  # type: ignore[arg-type]
        )
        assert not result.extraction_failed
        assert result.category == "requirements"

    def test_non_json_response_marks_failed(self) -> None:
        item = _make_context_item()
        result = asyncio.run(
            _extract_single_document(item, "Find APIs", _NonJsonExtractionLLM())  # type: ignore[arg-type]
        )
        assert result.extraction_failed
        assert result.doc_id == item.id
        assert result.title == item.title

    def test_exception_marks_failed(self) -> None:
        item = _make_context_item()
        result = asyncio.run(
            _extract_single_document(item, "Find APIs", _ExceptionLLM())  # type: ignore[arg-type]
        )
        assert result.extraction_failed

    def test_doc_id_and_title_preserved_on_failure(self) -> None:
        item = _make_context_item(doc_id="doc:SPECIAL", title="My Important Doc")
        result = asyncio.run(
            _extract_single_document(item, "goal", _ExceptionLLM())  # type: ignore[arg-type]
        )
        assert result.extraction_failed
        assert result.doc_id == item.id
        assert result.title == "My Important Doc"

    def test_empty_response_marks_failed(self) -> None:
        item = _make_context_item()
        result = asyncio.run(
            _extract_single_document(item, "Find APIs", _EmptyResponseLLM())  # type: ignore[arg-type]
        )
        assert result.extraction_failed

    def test_whitespace_only_response_marks_failed(self) -> None:
        item = _make_context_item()
        result = asyncio.run(
            _extract_single_document(item, "Find APIs", _WhitespaceOnlyLLM())  # type: ignore[arg-type]
        )
        assert result.extraction_failed

    def test_none_response_marks_failed(self) -> None:
        item = _make_context_item()
        result = asyncio.run(
            _extract_single_document(item, "Find APIs", _NoneResponseLLM())  # type: ignore[arg-type]
        )
        assert result.extraction_failed


# ---------------------------------------------------------------------------
# _extract_all_documents tests
# ---------------------------------------------------------------------------


class TestExtractAllDocuments:
    def test_all_succeed(self) -> None:
        items = [_make_context_item(doc_id=f"doc:SRC{i}") for i in range(3)]
        results = asyncio.run(
            _extract_all_documents(items, "goal", _ValidExtractionLLM(), concurrency=2)  # type: ignore[arg-type]
        )
        assert len(results) == 3
        assert all(not r.extraction_failed for r in results)

    def test_partial_failure_continues(self) -> None:
        items = [_make_context_item(doc_id=f"doc:SRC{i}") for i in range(3)]
        results = asyncio.run(
            _extract_all_documents(
                items,
                "goal",
                _NonJsonExtractionLLM(),
                concurrency=2,  # type: ignore[arg-type]
            )
        )
        assert len(results) == 3
        assert all(r.extraction_failed for r in results)

    def test_empty_items_returns_empty_list(self) -> None:
        results = asyncio.run(
            _extract_all_documents([], "goal", _ValidExtractionLLM(), concurrency=2)  # type: ignore[arg-type]
        )
        assert results == []


# ---------------------------------------------------------------------------
# _synthesize_accumulated tests
# ---------------------------------------------------------------------------


_FULL_SYNTHESIS_PAYLOAD = {
    "goals": [
        "Identify all REST API endpoints in the codebase",
        "Map authentication flows across system components",
    ],
    "themes": ["API-first architecture", "Security-driven design"],
    "doc_categories": {"Test Doc": "technical"},
    "structure_hints": {
        "has_requirements": False,
        "has_transcripts": False,
        "has_technical_specs": True,
        "has_user_feedback": False,
        "primary_domain": "Banking",
    },
    "input_evidence": [
        {"statement": "JWT is used for auth.", "source_doc_ids": ["doc:SRC1"]}
    ],
    "consolidated_findings": [
        {"finding": "Auth is central theme.", "source_doc_ids": ["doc:SRC1"]}
    ],
    "inferences": [
        {
            "inference": "System is API-first.",
            "because": "All docs discuss REST APIs.",
            "source_doc_ids": ["doc:SRC1"],
            "confidence": "high",
        }
    ],
    "conflicts": [],
    "gaps": [{"gap": "No test plan found.", "why_it_matters": "Cannot validate QA."}],
    "intent_alignment": {
        "user_prompt": "Find APIs",
        "interpretation": "Focus on API endpoints.",
        "priority_areas": ["endpoints"],
        "tensions": [],
    },
    "recommended_actions": [
        {
            "action": "Extract all API endpoints.",
            "rationale": "Foundation.",
            "priority": "high",
        }
    ],
}


class _ValidSynthesisLLM:
    provider = "bedrock"

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        return json.dumps(_FULL_SYNTHESIS_PAYLOAD)


class _FencedSynthesisLLM:
    provider = "bedrock"

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        return f"```json\n{json.dumps(_FULL_SYNTHESIS_PAYLOAD)}\n```"


class _NonJsonSynthesisLLM:
    provider = "bedrock"

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        return "I cannot synthesize this corpus at this time."


class TestSynthesizeAccumulated:
    def _extractions(self) -> list[DocExtraction]:
        return [_make_extraction("doc:SRC1", "Test Doc")]

    def _items(self) -> list[build_module.ContextItem]:
        return [_make_context_item()]

    def test_valid_synthesis_populates_result(self) -> None:
        result = asyncio.run(
            _synthesize_accumulated(
                self._extractions(),
                self._items(),
                "Find APIs",
                _ValidSynthesisLLM(),  # type: ignore[arg-type]
            )
        )
        assert len(result.goals) == 2
        assert "Identify all REST API endpoints in the codebase" in result.goals
        assert len(result.input_evidence) == 1
        assert result.structure_hints["primary_domain"] == "Banking"

    def test_fenced_response_handled(self) -> None:
        result = asyncio.run(
            _synthesize_accumulated(
                self._extractions(),
                self._items(),
                "Find APIs",
                _FencedSynthesisLLM(),  # type: ignore[arg-type]
            )
        )
        assert result.goals

    def test_non_json_falls_back_to_direct_injection(self) -> None:
        """When merge LLM returns non-JSON, evidence is injected directly."""
        result = asyncio.run(
            _synthesize_accumulated(
                self._extractions(),
                self._items(),
                "Find APIs",
                _NonJsonSynthesisLLM(),  # type: ignore[arg-type]
            )
        )
        assert result.input_evidence


# ---------------------------------------------------------------------------
# _analysis_from_extractions fallback tests
# ---------------------------------------------------------------------------


class TestAnalysisFromExtractions:
    def test_evidence_populated_from_successful_extractions(self) -> None:
        extractions = [_make_extraction("doc:SRC1", "Doc A")]
        items = [_make_context_item("doc:SRC1", "Doc A")]
        result = _analysis_from_extractions(extractions, items, "Find APIs")
        assert len(result.input_evidence) >= 1
        assert result.input_evidence[0]["source_doc_ids"] == ["doc:SRC1"]

    def test_failed_extractions_noted_in_gaps(self) -> None:
        extractions = [
            _make_extraction("doc:SRC1", "Doc A"),
            _make_extraction("doc:SRC2", "Doc B", failed=True),
        ]
        items = [
            _make_context_item("doc:SRC1", "Doc A"),
            _make_context_item("doc:SRC2", "Doc B"),
        ]
        result = _analysis_from_extractions(extractions, items, "goal")
        assert any("could not be extracted" in g.get("gap", "") for g in result.gaps)

    def test_all_failed_returns_empty_evidence_but_has_gaps(self) -> None:
        extractions = [
            _make_extraction("doc:SRC1", "Doc A", failed=True),
            _make_extraction("doc:SRC2", "Doc B", failed=True),
        ]
        items = [
            _make_context_item("doc:SRC1", "Doc A"),
            _make_context_item("doc:SRC2", "Doc B"),
        ]
        result = _analysis_from_extractions(extractions, items, "goal")
        assert result.input_evidence == []
        assert result.gaps
        assert result.goals

    def test_doc_categories_from_extractions(self) -> None:
        extractions = [_make_extraction("doc:SRC1", "Doc A")]
        items = [_make_context_item("doc:SRC1", "Doc A")]
        result = _analysis_from_extractions(extractions, items, "goal")
        assert result.doc_categories.get("Doc A") == "technical"

    def test_themes_from_key_concepts(self) -> None:
        extractions = [_make_extraction("doc:SRC1", "Doc A")]
        items = [_make_context_item("doc:SRC1", "Doc A")]
        result = _analysis_from_extractions(extractions, items, "goal")
        assert "REST API" in result.themes or "authentication" in result.themes


# ---------------------------------------------------------------------------
# Full analyze_corpus integration tests
# ---------------------------------------------------------------------------


class _TwoPhaseHappyLLM:
    """LLM that handles both per-doc and synthesis calls."""

    provider = "bedrock"
    _call_count = 0

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        self._call_count += 1
        user_msg = args[0] if args else kwargs.get("user_message", "")
        if "DOCUMENT CONTENT" in user_msg:
            return json.dumps(
                {
                    "category": "technical",
                    "summary": "API architecture document.",
                    "key_concepts": ["REST", "JWT"],
                    "evidence": [
                        {"statement": "System uses JWT.", "relevance": "Security."}
                    ],
                }
            )
        return json.dumps(_FULL_SYNTHESIS_PAYLOAD)


class _TwoPhaseSynthesisFailLLM:
    """Per-doc extractions succeed, synthesis fails."""

    provider = "bedrock"

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        user_msg = args[0] if args else kwargs.get("user_message", "")
        if "DOCUMENT CONTENT" in user_msg:
            return json.dumps(
                {
                    "category": "technical",
                    "summary": "API doc.",
                    "key_concepts": ["REST"],
                    "evidence": [{"statement": "Fact.", "relevance": "Relevant."}],
                }
            )
        return "synthesis failed — cannot produce JSON right now"


class TestAnalyzeCorpus:
    def _items(self, n: int = 2) -> list[build_module.ContextItem]:
        raw = [
            ExtractedItem(
                source_type="confluence",
                source_id=f"SRC{i}",
                title=f"Document {i}",
                body=f"Body of document {i} with content.",
            )
            for i in range(n)
        ]
        return [build_module.ContextItem.from_extracted(item, "proj") for item in raw]

    def test_happy_path_returns_full_result(self) -> None:
        items = self._items(2)
        result = asyncio.run(
            analyze_corpus(
                items=items,
                goal_prompt="Find APIs",
                llm=_TwoPhaseHappyLLM(),  # type: ignore[arg-type]
                model="model",
                provider="bedrock",
            )
        )
        assert result.goals
        assert result.input_evidence
        assert result.structure_hints.get("primary_domain") == "Banking"

    def test_synthesis_failure_falls_back_to_extractions(self) -> None:
        items = self._items(2)
        result = asyncio.run(
            analyze_corpus(
                items=items,
                goal_prompt="Find APIs",
                llm=_TwoPhaseSynthesisFailLLM(),  # type: ignore[arg-type]
                model="model",
                provider="bedrock",
            )
        )
        # Should still have evidence from per-doc extraction
        assert result.goals
        assert result.input_evidence

    def test_dummy_provider_uses_fallback(self) -> None:
        items = self._items(2)
        result = asyncio.run(
            analyze_corpus(
                items=items,
                goal_prompt="Find APIs",
                llm=_TwoPhaseHappyLLM(),  # type: ignore[arg-type]
                model="dummy",
                provider="dummy",
            )
        )
        assert result.goals
        assert result.intent_alignment.get("user_prompt") == "Find APIs"

    def test_empty_items_returns_goal_from_prompt(self) -> None:
        result = asyncio.run(
            analyze_corpus(
                items=[],
                goal_prompt="Find APIs",
                llm=_TwoPhaseHappyLLM(),  # type: ignore[arg-type]
                model="model",
                provider="bedrock",
            )
        )
        assert "Find APIs" in result.goals

    def test_goal_prompt_not_echoed_verbatim(self) -> None:
        items = self._items(2)
        user_prompt = "Find APIs"
        result = asyncio.run(
            analyze_corpus(
                items=items,
                goal_prompt=user_prompt,
                llm=_TwoPhaseHappyLLM(),  # type: ignore[arg-type]
                model="model",
                provider="bedrock",
            )
        )
        assert all(goal.casefold() != user_prompt.casefold() for goal in result.goals)

    def test_per_doc_max_tokens_passed_through(self) -> None:
        """Verify custom per_doc_max_tokens doesn't break the flow."""
        items = self._items(2)
        result = asyncio.run(
            analyze_corpus(
                items=items,
                goal_prompt="Find APIs",
                llm=_TwoPhaseHappyLLM(),  # type: ignore[arg-type]
                model="model",
                provider="bedrock",
                per_doc_max_tokens=1024,
                analysis_concurrency=2,
            )
        )
        assert result.goals


# ---------------------------------------------------------------------------
# Integration: full build_context with new analyzer
# ---------------------------------------------------------------------------


def test_build_context_with_incremental_analysis(
    dummy_config: ContextConfig,
    sample_items: list[ExtractedItem],
) -> None:
    """Ensure build_context completes end-to-end with the dummy (fallback) provider."""
    request = ContextBuildRequest(
        project="proj",
        goal_prompt="Understand the system architecture",
        extracted_items=sample_items,
    )
    result = asyncio.run(build_context(request, dummy_config))
    assert result.status in {"success", "partial_success"}
    assert result.goals
    assert "Understand the system architecture" not in result.goals


def test_non_json_extraction_falls_back_to_extractions_based_result(
    sample_items: list[ExtractedItem],
) -> None:
    """When synthesis LLM returns non-JSON, result is built from per-doc extractions."""
    context_items = [
        build_module.ContextItem.from_extracted(item, "proj") for item in sample_items
    ]
    result = asyncio.run(
        analyze_corpus(
            items=context_items,
            goal_prompt="Understand architecture",
            llm=_TwoPhaseSynthesisFailLLM(),  # type: ignore[arg-type]
            model="model",
            provider="bedrock",
        )
    )
    assert result.goals
    assert result.intent_alignment.get("user_prompt") == "Understand architecture"


# ---------------------------------------------------------------------------
# Edge case / hardening tests
# ---------------------------------------------------------------------------


from evoq_context.synthesis.analyzer import (
    _cap_running_state,
    _direct_inject,
    _safe_merge_state,
)


class TestSafeMergeState:
    def test_preserves_evidence_if_llm_drops_it(self) -> None:
        previous = {
            "input_evidence": [{"statement": "fact A", "source_doc_ids": ["d1"]}],
            "goals": ["Goal 1"],
            "themes": ["Theme 1"],
        }
        updated = {
            "input_evidence": [],
            "goals": ["Goal 2"],
            "themes": [],
        }
        result = _safe_merge_state(previous, updated)
        assert result["input_evidence"] == previous["input_evidence"]
        assert result["goals"] == ["Goal 2"]
        assert result["themes"] == previous["themes"]

    def test_preserves_doc_categories_accumulative(self) -> None:
        previous = {"doc_categories": {"DocA": "technical"}}
        updated = {"doc_categories": {"DocB": "requirements"}}
        result = _safe_merge_state(previous, updated)
        assert result["doc_categories"] == {
            "DocA": "technical",
            "DocB": "requirements",
        }

    def test_keeps_structure_hints_if_dropped(self) -> None:
        previous = {"structure_hints": {"primary_domain": "banking"}}
        updated = {}
        result = _safe_merge_state(previous, updated)
        assert result["structure_hints"]["primary_domain"] == "banking"

    def test_accepts_valid_update(self) -> None:
        previous = {"input_evidence": [{"statement": "A"}], "goals": []}
        updated = {
            "input_evidence": [{"statement": "A"}, {"statement": "B"}],
            "goals": ["New goal"],
        }
        result = _safe_merge_state(previous, updated)
        assert len(result["input_evidence"]) == 2
        assert result["goals"] == ["New goal"]


class TestCapRunningState:
    def test_caps_oversized_lists(self) -> None:
        state = {"input_evidence": [{"s": str(i)} for i in range(50)]}
        _cap_running_state(state)
        assert len(state["input_evidence"]) == 25

    def test_does_not_touch_small_lists(self) -> None:
        state = {"goals": ["a", "b"], "themes": ["x"]}
        _cap_running_state(state)
        assert state["goals"] == ["a", "b"]
        assert state["themes"] == ["x"]


class TestDirectInject:
    def test_injects_evidence_and_themes(self) -> None:
        state: dict[str, Any] = {
            "doc_categories": {},
            "input_evidence": [],
            "themes": [],
        }
        ext = _make_extraction("doc:X", "My Doc")
        _direct_inject(state, ext)
        assert state["doc_categories"]["My Doc"] == "technical"
        assert len(state["input_evidence"]) >= 1
        assert any(t in state["themes"] for t in ["REST API", "authentication", "JWT"])

    def test_does_not_exceed_theme_cap(self) -> None:
        state: dict[str, Any] = {
            "doc_categories": {},
            "input_evidence": [],
            "themes": ["a", "b", "c", "d", "e", "f", "g"],
        }
        ext = _make_extraction("doc:X", "Doc")
        _direct_inject(state, ext)
        assert len(state["themes"]) == 7


class _MergeReturnsPartialLLM:
    """LLM that returns valid JSON but drops some keys."""

    provider = "bedrock"

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        return json.dumps(
            {
                "goals": ["Identify API endpoints"],
                "themes": ["REST architecture"],
            }
        )


class _MergeReturnsArrayLLM:
    """LLM that returns a JSON array instead of object."""

    provider = "bedrock"

    async def complete(self, *args: Any, **kwargs: Any) -> str:
        return json.dumps([{"bad": "shape"}])


class TestIterativeMergeEdgeCases:
    def _items(self, n: int = 2) -> list[build_module.ContextItem]:
        raw = [
            ExtractedItem(
                source_type="confluence",
                source_id=f"SRC{i}",
                title=f"Document {i}",
                body=f"Body of document {i} with content.",
            )
            for i in range(n)
        ]
        return [build_module.ContextItem.from_extracted(item, "proj") for item in raw]

    def test_all_extractions_failed_uses_extraction_fallback(self) -> None:
        items = self._items(3)
        result = asyncio.run(
            analyze_corpus(
                items=items,
                goal_prompt="Find APIs",
                llm=_ExceptionLLM(),  # type: ignore[arg-type]
                model="model",
                provider="bedrock",
            )
        )
        # Should still produce something (fallback from extractions)
        assert result.goals
        assert result.intent_alignment.get("user_prompt") == "Find APIs"

    def test_partial_merge_preserves_data_from_successful_docs(self) -> None:
        """Even if merge LLM returns partial keys, data is preserved."""
        items = self._items(2)
        result = asyncio.run(
            analyze_corpus(
                items=items,
                goal_prompt="Find APIs",
                llm=_MergeReturnsPartialLLM(),  # type: ignore[arg-type]
                model="model",
                provider="bedrock",
            )
        )
        assert result.goals

    def test_merge_returns_array_triggers_direct_inject(self) -> None:
        """If merge LLM returns array, direct injection kicks in."""
        items = self._items(2)
        result = asyncio.run(
            analyze_corpus(
                items=items,
                goal_prompt="Find APIs",
                llm=_MergeReturnsArrayLLM(),  # type: ignore[arg-type]
                model="model",
                provider="bedrock",
            )
        )
        assert result.goals

    def test_large_corpus_state_stays_bounded(self) -> None:
        """With many docs, running state doesn't grow unbounded."""
        items = self._items(15)
        result = asyncio.run(
            analyze_corpus(
                items=items,
                goal_prompt="Find APIs",
                llm=_TwoPhaseHappyLLM(),  # type: ignore[arg-type]
                model="model",
                provider="bedrock",
            )
        )
        assert len(result.input_evidence) <= 25
        assert len(result.goals) <= 10

    def test_empty_corpus_returns_valid_result(self) -> None:
        """analyze_corpus with no items should not crash."""
        result = asyncio.run(
            analyze_corpus(
                items=[],
                goal_prompt="Find APIs",
                llm=_TwoPhaseHappyLLM(),  # type: ignore[arg-type]
                model="model",
                provider="bedrock",
            )
        )
        assert isinstance(result, AnalysisResult)
        assert result.intent_alignment.get("user_prompt") == "Find APIs"

    def test_single_item_corpus(self) -> None:
        """One document should work end-to-end."""
        items = self._items(1)
        result = asyncio.run(
            analyze_corpus(
                items=items,
                goal_prompt="Summarize",
                llm=_TwoPhaseHappyLLM(),  # type: ignore[arg-type]
                model="model",
                provider="bedrock",
            )
        )
        assert result.goals
        assert result.doc_categories

    def test_empty_llm_response_triggers_direct_inject(self) -> None:
        """Empty LLM responses during merge use direct injection fallback."""
        items = self._items(2)
        result = asyncio.run(
            analyze_corpus(
                items=items,
                goal_prompt="Find APIs",
                llm=_EmptyResponseLLM(),  # type: ignore[arg-type]
                model="model",
                provider="bedrock",
            )
        )
        # Should still produce result from extraction fallback
        assert isinstance(result, AnalysisResult)
        assert result.goals

    def test_whitespace_llm_response_triggers_fallback(self) -> None:
        """Whitespace-only responses are handled gracefully."""
        items = self._items(2)
        result = asyncio.run(
            analyze_corpus(
                items=items,
                goal_prompt="Find APIs",
                llm=_WhitespaceOnlyLLM(),  # type: ignore[arg-type]
                model="model",
                provider="bedrock",
            )
        )
        assert isinstance(result, AnalysisResult)
        assert result.goals
