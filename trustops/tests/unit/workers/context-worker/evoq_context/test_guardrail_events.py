"""Plan §3.1a: guardrail events for state-merge corrections and degraded analysis.

Drives ``_safe_merge_state`` directly on a state-dropping LLM response and
``AnalysisStage`` into a degraded path, asserting the exact
``corrected``/``rejected``/``degraded`` events fire with the expected shape.
"""

from __future__ import annotations

import asyncio

import pytest
from evoq_context.core.config import ContextConfig
from evoq_context.core.contracts import ContextBuildRequest
from evoq_context.core.observer import GuardrailEvent, LlmCallRecord
from evoq_context.llm.client import LLMClient
from evoq_context.pipeline.context import BuildContext
from evoq_context.pipeline.plan import BuildPlanner
from evoq_context.pipeline.stages import AnalysisStage
from evoq_context.pipeline.tracker import PipelineTracker
from evoq_context.synthesis.analyzer import (
    AnalysisResult,
    RunningAnalysisState,
    _safe_merge_state,
)


class RecordingObserver:
    """Minimal ``ContextObserver`` double recording every call/event."""

    def __init__(self) -> None:
        self.llm_calls: list[LlmCallRecord] = []
        self.guardrail_events: list[GuardrailEvent] = []
        self.stage_events: list[tuple[str, str]] = []

    def stage_start(self, name: str, attrs) -> object:
        return name

    def stage_end(
        self, handle: object, *, status: str, duration_ms: int, error=None
    ) -> None:
        self.stage_events.append((str(handle), status))

    def document_done(self, record) -> None:
        return None

    def build_summary(self, summary) -> None:
        return None

    def metric(self, name: str, value: float, attrs) -> None:
        return None

    def on_llm_call(self, record: LlmCallRecord) -> None:
        self.llm_calls.append(record)

    def on_guardrail_event(self, event: GuardrailEvent) -> None:
        self.guardrail_events.append(event)


# ---------------------------------------------------------------------------
# _safe_merge_state — "corrected" (partial drop) and "rejected" (total drop)
# ---------------------------------------------------------------------------


def test_safe_merge_state_emits_corrected_event_on_partial_state_drop() -> None:
    observer = RecordingObserver()
    previous = RunningAnalysisState(
        goals=["Goal 1"],
        input_evidence=[{"statement": "fact A", "source_doc_ids": ["d1"]}],
    )
    # The LLM's merge response drops input_evidence entirely but keeps goals.
    updated = {"goals": ["Goal 2"], "input_evidence": []}

    result = _safe_merge_state(previous, updated, observer=observer)

    assert result.input_evidence == previous.input_evidence
    assert result.goals == ["Goal 2"]
    assert len(observer.guardrail_events) == 1
    event = observer.guardrail_events[0]
    assert event.name == "safe_merge_state"
    assert event.outcome == "corrected"
    assert "input_evidence" in event.subject
    assert "input_evidence" in event.detail


def test_safe_merge_state_emits_rejected_event_when_every_tracked_key_is_dropped() -> (
    None
):
    observer = RecordingObserver()
    previous = RunningAnalysisState(
        goals=["Goal 1"],
        themes=["Theme 1"],
        input_evidence=[{"statement": "fact A"}],
        consolidated_findings=[{"finding": "f"}],
        inferences=[{"inference": "i"}],
        conflicts=[{"topic": "t"}],
        gaps=[{"gap": "g"}],
        recommended_actions=[{"action": "a"}],
    )
    # The LLM's merge response contributes nothing at all — every tracked
    # key comes back empty, so the merge is effectively rejected wholesale.
    updated: dict = {
        "goals": [],
        "themes": [],
        "input_evidence": [],
        "consolidated_findings": [],
        "inferences": [],
        "conflicts": [],
        "gaps": [],
        "recommended_actions": [],
    }

    result = _safe_merge_state(previous, updated, observer=observer)

    assert result.goals == previous.goals
    assert result.input_evidence == previous.input_evidence
    assert len(observer.guardrail_events) == 1
    event = observer.guardrail_events[0]
    assert event.name == "safe_merge_state"
    assert event.outcome == "rejected"


def test_safe_merge_state_emits_no_event_when_nothing_needed_correcting() -> None:
    observer = RecordingObserver()
    previous = RunningAnalysisState(goals=["Goal 1"])
    updated = {"goals": ["Goal 2"], "input_evidence": [{"statement": "new fact"}]}

    _safe_merge_state(previous, updated, observer=observer)

    assert observer.guardrail_events == []


def test_safe_merge_state_works_without_an_observer() -> None:
    """No observer supplied — the existing correction logic is unchanged, just unobserved."""
    previous = {"goals": ["Goal 1"]}
    updated = {"goals": []}
    result = _safe_merge_state(previous, updated)
    assert result.goals == ["Goal 1"]


# ---------------------------------------------------------------------------
# AnalysisStage -> degraded path
# ---------------------------------------------------------------------------


def _dummy_config(tmp_path) -> ContextConfig:
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
        chunk_target_tokens=700,
        chunk_hard_max_tokens=2000,
        chunk_max_rows=500,
        llm_max_tokens=15000,
        analysis_model="dummy",
        analysis_provider="dummy",
        synthesis_model="dummy",
        synthesis_provider="dummy",
    )


def _build_context_for_stage(tmp_path) -> BuildContext:
    config = _dummy_config(tmp_path)
    request = ContextBuildRequest(
        project="proj", goal_prompt="goal", extracted_items=[]
    )
    plan = BuildPlanner(config).plan(request)
    observer = RecordingObserver()
    tracker = PipelineTracker(observer)
    return BuildContext(
        request=request,
        config=config,
        plan=plan,
        tracker=tracker,
        observer=observer,
        llm=LLMClient.from_config(config),
        vector_store=None,
    )


def test_analysis_stage_degraded_emits_guardrail_event_and_partial_success(
    tmp_path,
    monkeypatch,
) -> None:
    ctx = _build_context_for_stage(tmp_path)

    async def _degraded_analysis(*args, **kwargs):
        return AnalysisResult(
            goals=["A goal"],
            degraded=True,
            docs_extracted=2,
            docs_failed=1,
            merge_batches_failed=1,
            proofread_failed=True,
        )

    import evoq_context.pipeline.stages as stages_module

    monkeypatch.setattr(stages_module, "analyze_corpus", _degraded_analysis)

    stage = AnalysisStage()
    asyncio.run(stage.run(ctx))

    assert ctx.analysis.degraded is True
    assert ctx.tracker.partial_success is True
    degraded_components = [c for c in ctx.tracker.components if c.status == "degraded"]
    assert len(degraded_components) == 1
    assert degraded_components[0].name == "analysis"
    assert degraded_components[0].error_code == "ANALYSIS_DEGRADED"

    observer: RecordingObserver = ctx.observer  # type: ignore[assignment]
    degraded_events = [
        e for e in observer.guardrail_events if e.name == "analysis_degraded"
    ]
    assert len(degraded_events) == 1
    event = degraded_events[0]
    assert event.outcome == "degraded"
    assert "docs_failed=1" in event.detail
    assert "merge_batches_failed=1" in event.detail
    assert "proofread_failed=True" in event.detail


def test_analysis_stage_clean_run_reports_success_not_degraded(
    tmp_path, monkeypatch
) -> None:
    ctx = _build_context_for_stage(tmp_path)

    async def _clean_analysis(*args, **kwargs):
        return AnalysisResult(goals=["A goal"])

    import evoq_context.pipeline.stages as stages_module

    monkeypatch.setattr(stages_module, "analyze_corpus", _clean_analysis)

    stage = AnalysisStage()
    asyncio.run(stage.run(ctx))

    assert ctx.tracker.partial_success is False
    statuses = {c.name: c.status for c in ctx.tracker.components}
    assert statuses["analysis"] == "success"
    observer: RecordingObserver = ctx.observer  # type: ignore[assignment]
    assert not any(e.name == "analysis_degraded" for e in observer.guardrail_events)


# ---------------------------------------------------------------------------
# End-to-end: a real merge-batch failure (non-JSON LLM response) surfaces
# both the "corrected" safe_merge_state event (proofread pass) and the
# analyzer's own docs_failed/degraded bookkeeping, via analyze_corpus.
# ---------------------------------------------------------------------------


class _FlakyMergeLLM:
    """Per-doc extraction succeeds; the merge call returns garbage (forcing
    direct-injection fallback), then a valid, state-dropping proofread.

    Records the ``caller=`` every ``complete()`` call was made with, so the
    test can assert observer/caller plumbing reaches this call site (this
    stub does not itself implement ``ContextObserver`` forwarding — only
    the real ``LLMClient`` emits ``LlmCallRecord``s; this is what proves
    ``analyzer.py`` actually threads ``observer=``/``caller=`` through to
    whatever LLM client it's given, real or fake).
    """

    provider = "bedrock"

    def __init__(self) -> None:
        self.calls = 0
        self.callers: list[str] = []

    async def complete(self, *args, **kwargs):
        self.calls += 1
        self.callers.append(kwargs.get("caller", ""))
        user_msg = args[0] if args else kwargs.get("user_message", "")
        if "DOCUMENT CONTENT" in user_msg:
            return (
                '{"category": "technical", "summary": "doc", '
                '"key_concepts": ["X"], "evidence": [{"statement": "fact", "relevance": "r"}]}'
            )
        if "NEW DOCUMENTS TO INCORPORATE" in user_msg:
            return "not-json-at-all"
        # Proofread: valid JSON, but drops every list field the merge phase
        # already accumulated via direct injection.
        return '{"goals": [], "input_evidence": [], "themes": []}'


@pytest.mark.asyncio
async def test_analyze_corpus_reports_docs_extracted_and_merge_batch_failure(
    tmp_path,
) -> None:
    from evoq_context.core.models import ContextItem
    from evoq_context.synthesis.analyzer import analyze_corpus

    items = [
        ContextItem(
            id=f"file:D{i}",
            source_type="file",
            source_id=f"D{i}",
            title=f"Doc {i}",
            body=f"Body {i}",
            content_hash=f"h{i}",
            project="proj",
        )
        for i in range(2)
    ]
    observer = RecordingObserver()
    llm = _FlakyMergeLLM()

    result = await analyze_corpus(
        items=items,
        goal_prompt="goal",
        llm=llm,
        model="model",
        provider="bedrock",
        observer=observer,
    )

    assert result.docs_extracted == 2
    assert result.docs_failed == 0
    assert result.merge_batches_failed >= 1
    assert result.degraded is True

    # Confirms analyzer.py threads observer=/caller= through to whatever
    # LLM client it's given for all three phases (plan §3.1a).
    assert "analyzer.extract_document" in llm.callers
    assert "analyzer.merge_batch" in llm.callers
    assert "analyzer.proofread" in llm.callers
