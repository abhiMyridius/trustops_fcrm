"""Observer seam between the engine and whatever telemetry the host runs.

Scope & functionality
---------------------
The engine never imports OpenTelemetry or a host logger. Instead every
stage, document, LLM call and guardrail decision is reported through the
``ContextObserver`` Protocol defined here, and the host binds an adapter
(``aeocontextjob/observability/otel_observer.py``) that turns those calls
into spans/metrics/log lines. ``NullObserver`` is the default (no-op);
``LoggingObserver`` is a stdlib-only implementation for scripts and tests.

Records are frozen dataclasses so an adapter can rely on their shape:
``DocumentRecord`` (one per document), ``LlmCallRecord`` (one per
embed/complete call — token usage, latency, attempt), ``GuardrailEvent``
(one per place the engine silently corrects/rejects/degrades something)
and ``BuildSummary`` (emitted exactly once per build, rolled up from the
same stream — no second summary object anywhere).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

from evoq_context.core.contracts import ErrorDetail
from evoq_context.core.models import JsonValue

AttrValue = str | int | float | bool
Attrs = Mapping[str, AttrValue]

StageStatus = Literal["success", "error", "skipped", "degraded"]
GuardrailOutcome = Literal["passed", "corrected", "rejected", "degraded"]
LlmOperation = Literal["embed", "complete"]


@dataclass(frozen=True, slots=True)
class DocumentRecord:
    """One document's chunk/embed outcome, emitted when its vectors are written."""

    doc_id: str
    strategy: str
    chunk_count: int
    parent_count: int = 0
    token_p50: int = 0
    token_p95: int = 0
    duration_ms: int = 0
    status: str = "success"
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class LlmCallRecord:
    """One LLM call (embedding or completion) with its token usage.

    ``attempt`` is 1-based; ``retries`` is how many earlier attempts failed.
    Token counts come from the provider's ``usage`` block; when a provider
    omits it they are recorded as ``0`` with ``warning="usage_unavailable"``.
    """

    operation: LlmOperation
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    attempt: int
    retries: int
    success: bool
    caller: str
    error_code: str | None = None
    doc_id: str | None = None
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class GuardrailEvent:
    """A place where the engine decided to pass/correct/reject/degrade."""

    name: str
    subject: str
    outcome: GuardrailOutcome
    detail: str


@dataclass(slots=True)
class BuildSummary:
    """Everything an operator needs about one build, emitted exactly once."""

    project: str
    version: str
    mode: str
    status: str
    duration_ms: int = 0
    documents_received: int = 0
    new: int = 0
    updated: int = 0
    deleted: int = 0
    unchanged: int = 0
    failed_docs: int = 0
    failed_codes: dict[str, int] = field(default_factory=dict)
    chunks_created: int = 0
    parents_stored: int = 0
    vectors_upserted: int = 0
    strategy_histogram: dict[str, int] = field(default_factory=dict)
    token_p50: int = 0
    token_p95: int = 0
    token_max: int = 0
    hard_max_hits: int = 0
    topic_boundaries: int = 0
    stage_durations_ms: dict[str, int] = field(default_factory=dict)
    errors: list[ErrorDetail] = field(default_factory=list)
    llm_calls: int = 0
    llm_tokens_total: int = 0
    llm_calls_by_caller: dict[str, int] = field(default_factory=dict)
    guardrail_events_by_outcome: dict[str, int] = field(default_factory=dict)

    def record_llm_call(self, record: LlmCallRecord) -> None:
        """Roll one ``LlmCallRecord`` into the summary counters."""
        self.llm_calls += 1
        self.llm_tokens_total += record.prompt_tokens + record.completion_tokens
        self.llm_calls_by_caller[record.caller] = self.llm_calls_by_caller.get(record.caller, 0) + 1

    def record_guardrail(self, event: GuardrailEvent) -> None:
        """Roll one ``GuardrailEvent`` into the per-outcome counters."""
        self.guardrail_events_by_outcome[event.outcome] = (
            self.guardrail_events_by_outcome.get(event.outcome, 0) + 1
        )

    def to_record(self) -> dict[str, JsonValue]:
        """Flat, JSON-safe form for a ``build_summary`` log line / job record."""
        return {
            "project": self.project,
            "version": self.version,
            "mode": self.mode,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "documents_received": self.documents_received,
            "new": self.new,
            "updated": self.updated,
            "deleted": self.deleted,
            "unchanged": self.unchanged,
            "failed_docs": self.failed_docs,
            "failed_codes": dict(self.failed_codes),
            "chunks_created": self.chunks_created,
            "parents_stored": self.parents_stored,
            "vectors_upserted": self.vectors_upserted,
            "strategy_histogram": dict(self.strategy_histogram),
            "token_p50": self.token_p50,
            "token_p95": self.token_p95,
            "token_max": self.token_max,
            "hard_max_hits": self.hard_max_hits,
            "topic_boundaries": self.topic_boundaries,
            "stage_durations_ms": dict(self.stage_durations_ms),
            "errors": [
                {"stage": e.stage, "code": e.code, "message": e.message, "doc_id": e.doc_id}
                for e in self.errors
            ],
            "llm_calls": self.llm_calls,
            "llm_tokens_total": self.llm_tokens_total,
            "llm_calls_by_caller": dict(self.llm_calls_by_caller),
            "guardrail_events_by_outcome": dict(self.guardrail_events_by_outcome),
        }


@runtime_checkable
class ContextObserver(Protocol):
    """What the engine reports; the host decides what to do with it.

    ``stage_start`` returns an opaque handle (an OTel span for the host
    adapter, a small record for ``LoggingObserver``, ``None`` for
    ``NullObserver``) that is handed back to ``stage_end``.
    """

    def stage_start(self, name: str, attrs: Attrs) -> object: ...

    def stage_end(
        self,
        handle: object,
        *,
        status: StageStatus,
        duration_ms: int,
        error: BaseException | None = None,
    ) -> None: ...

    def document_done(self, record: DocumentRecord) -> None: ...

    def build_summary(self, summary: BuildSummary) -> None: ...

    def metric(self, name: str, value: float, attrs: Mapping[str, str]) -> None: ...

    def on_llm_call(self, record: LlmCallRecord) -> None: ...

    def on_guardrail_event(self, event: GuardrailEvent) -> None: ...


class NullObserver:
    """The default observer: every hook is a no-op."""

    def stage_start(self, name: str, attrs: Attrs) -> object:
        return None

    def stage_end(
        self,
        handle: object,
        *,
        status: StageStatus,
        duration_ms: int,
        error: BaseException | None = None,
    ) -> None:
        return None

    def document_done(self, record: DocumentRecord) -> None:
        return None

    def build_summary(self, summary: BuildSummary) -> None:
        return None

    def metric(self, name: str, value: float, attrs: Mapping[str, str]) -> None:
        return None

    def on_llm_call(self, record: LlmCallRecord) -> None:
        return None

    def on_guardrail_event(self, event: GuardrailEvent) -> None:
        return None


@dataclass(frozen=True, slots=True)
class _StageHandle:
    name: str
    attrs: dict[str, AttrValue]


def _fmt_attrs(attrs: Mapping[str, AttrValue]) -> str:
    return " ".join(f"{k}={v}" for k, v in sorted(attrs.items()))


class LoggingObserver:
    """Stdlib-``logging`` observer for scripts, the CLI and tests.

    Every hook is one lazily-formatted log record on the given logger;
    nothing here imports OpenTelemetry or the host's logging setup.
    """

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._logger = logger or logging.getLogger("evoq_context.observer")

    def stage_start(self, name: str, attrs: Attrs) -> object:
        self._logger.info("stage_start stage=%s %s", name, _fmt_attrs(attrs))
        return _StageHandle(name=name, attrs=dict(attrs))

    def stage_end(
        self,
        handle: object,
        *,
        status: StageStatus,
        duration_ms: int,
        error: BaseException | None = None,
    ) -> None:
        name = handle.name if isinstance(handle, _StageHandle) else "?"
        level = logging.INFO if status in ("success", "skipped") else logging.WARNING
        self._logger.log(
            level,
            "stage_end stage=%s status=%s duration_ms=%d error=%s",
            name,
            status,
            duration_ms,
            "" if error is None else f"{type(error).__name__}: {error}",
        )

    def document_done(self, record: DocumentRecord) -> None:
        self._logger.info(
            "document_done doc_id=%s strategy=%s chunks=%d parents=%d "
            "token_p50=%d token_p95=%d duration_ms=%d status=%s code=%s",
            record.doc_id,
            record.strategy,
            record.chunk_count,
            record.parent_count,
            record.token_p50,
            record.token_p95,
            record.duration_ms,
            record.status,
            record.failure_code or "",
        )

    def build_summary(self, summary: BuildSummary) -> None:
        self._logger.info("build_summary %s", summary.to_record())

    def metric(self, name: str, value: float, attrs: Mapping[str, str]) -> None:
        self._logger.debug("metric %s=%s %s", name, value, _fmt_attrs(attrs))

    def on_llm_call(self, record: LlmCallRecord) -> None:
        self._logger.info(
            "llm_call caller=%s operation=%s provider=%s model=%s prompt_tokens=%d "
            "completion_tokens=%d total_tokens=%d latency_ms=%d attempt=%d success=%s "
            "error_code=%s doc_id=%s",
            record.caller,
            record.operation,
            record.provider,
            record.model,
            record.prompt_tokens,
            record.completion_tokens,
            record.total_tokens,
            record.latency_ms,
            record.attempt,
            record.success,
            record.error_code or "",
            record.doc_id or "",
        )

    def on_guardrail_event(self, event: GuardrailEvent) -> None:
        level = logging.INFO if event.outcome == "passed" else logging.WARNING
        self._logger.log(
            level,
            "guardrail_event name=%s subject=%s outcome=%s detail=%s",
            event.name,
            event.subject,
            event.outcome,
            event.detail,
        )
