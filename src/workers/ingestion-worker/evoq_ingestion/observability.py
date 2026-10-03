"""Observer hooks the host binds to its own logger / tracer / metrics.

Scope & functionality
---------------------
The engine never imports a logging framework, OpenTelemetry or the host's
``applogs``/``apptracer`` (AST-enforced by ``tests/test_no_host_imports.py``).
Instead every pipeline stage reports through the ``IngestionObserver``
Protocol defined here: per-source start/end, per-file start/end
(``FileEvent``), warnings, spans, and — per plan §3.1a — every LLM call
(``LlmCallRecord``) and every guardrail decision (``GuardrailEvent``) the
engine already makes silently (scanned-PDF threshold, extension mismatch,
encoding fallback, ...). ``NullObserver`` is the default and does nothing;
the job's ``jobs/observer_adapter.py::HostObserver`` maps these events onto
structured log lines, OTel spans and instruments.

Observers are plain objects passed in via ``IngestionRequest.observer``; they
must never raise (the engine does not guard against a misbehaving observer).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

AttributeValue = str | int | float | bool
LlmOperation = Literal["embed", "complete"]
GuardrailOutcome = Literal["passed", "corrected", "rejected", "degraded"]


@dataclass(frozen=True, slots=True)
class FileEvent:
    """One file's extraction outcome, emitted by ``on_file_end``."""

    path: str
    engine: str
    engine_version: str
    raw_format: str
    status: str
    duration_ms: int
    bytes: int
    pages: int | None = None
    warnings: tuple[str, ...] = ()
    fallback_used: bool = False
    failure_code: str | None = None


@dataclass(frozen=True, slots=True)
class LlmCallRecord:
    """One LLM call (embedding or completion), plan §3.1a."""

    operation: LlmOperation
    provider: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    latency_ms: int
    caller: str
    attempt: int = 1
    retries: int = 0
    success: bool = True
    error_code: str | None = None
    doc_id: str | None = None
    warning: str | None = None


@dataclass(frozen=True, slots=True)
class GuardrailEvent:
    """A guardrail decision the engine made (plan §3.1a) — observable, not new logic."""

    name: str
    subject: str
    outcome: GuardrailOutcome
    detail: str = ""


@dataclass(frozen=True, slots=True)
class SourceSummary:
    """Roll-up passed to ``on_source_end``."""

    source: str
    duration_ms: int
    document_count: int
    errors: tuple[str, ...] = field(default=())


@runtime_checkable
class IngestionObserver(Protocol):
    """Hooks the host implements; every method is fire-and-forget."""

    def on_source_start(self, source: str) -> None:
        """Report that a source (``jira`` / ``confluence`` / ``uploads``) starts fetching."""

    def on_source_end(
        self,
        source: str,
        duration_ms: int,
        document_count: int,
        errors: Sequence[str],
    ) -> None:
        """Report that a source finished (successfully or not)."""

    def on_file_start(self, path: str, engine: str) -> None:
        """Report that an upload file is about to be extracted by *engine*."""

    def on_file_end(self, event: FileEvent) -> None:
        """Report that an upload file finished extraction (any status)."""

    def on_warning(self, scope: str, code: str, detail: str) -> None:
        """Report a warning code recorded for *scope* (a path, page key or source)."""

    def span(
        self,
        name: str,
        attributes: Mapping[str, AttributeValue] | None = None,
    ) -> AbstractContextManager[None]:
        """Open a tracing span; the host returns its tracer's context manager."""

    def on_llm_call(self, record: LlmCallRecord) -> None:
        """Report that one LLM call completed (success or failure)."""

    def on_guardrail_event(self, event: GuardrailEvent) -> None:
        """Report that a guardrail passed/corrected/rejected/degraded something."""


class NullObserver:
    """Default observer: every hook is a no-op, ``span`` is a null context."""

    __slots__ = ()

    def on_source_start(self, source: str) -> None:
        """No-op."""

    def on_source_end(
        self,
        source: str,
        duration_ms: int,
        document_count: int,
        errors: Sequence[str],
    ) -> None:
        """No-op."""

    def on_file_start(self, path: str, engine: str) -> None:
        """No-op."""

    def on_file_end(self, event: FileEvent) -> None:
        """No-op."""

    def on_warning(self, scope: str, code: str, detail: str) -> None:
        """No-op."""

    def span(
        self,
        name: str,
        attributes: Mapping[str, AttributeValue] | None = None,
    ) -> AbstractContextManager[None]:
        """Return a context manager that does nothing."""
        return nullcontext()

    def on_llm_call(self, record: LlmCallRecord) -> None:
        """No-op."""

    def on_guardrail_event(self, event: GuardrailEvent) -> None:
        """No-op."""


class RecordingObserver(NullObserver):
    """Test double that keeps every event in memory (used by the engine's own tests)."""

    __slots__ = ("files", "guardrails", "llm_calls", "sources", "spans", "warnings")

    def __init__(self) -> None:
        """Start with every event list empty."""
        self.sources: list[SourceSummary] = []
        self.files: list[FileEvent] = []
        self.warnings: list[tuple[str, str, str]] = []
        self.spans: list[str] = []
        self.llm_calls: list[LlmCallRecord] = []
        self.guardrails: list[GuardrailEvent] = []

    def on_source_end(
        self,
        source: str,
        duration_ms: int,
        document_count: int,
        errors: Sequence[str],
    ) -> None:
        """Record the source roll-up."""
        self.sources.append(SourceSummary(source, duration_ms, document_count, tuple(errors)))

    def on_file_end(self, event: FileEvent) -> None:
        """Record the file event."""
        self.files.append(event)

    def on_warning(self, scope: str, code: str, detail: str) -> None:
        """Record the warning."""
        self.warnings.append((scope, code, detail))

    def span(
        self,
        name: str,
        attributes: Mapping[str, AttributeValue] | None = None,
    ) -> AbstractContextManager[None]:
        """Record the span name and return a null context."""
        self.spans.append(name)
        return nullcontext()

    def on_llm_call(self, record: LlmCallRecord) -> None:
        """Record the LLM call."""
        self.llm_calls.append(record)

    def on_guardrail_event(self, event: GuardrailEvent) -> None:
        """Record the guardrail event."""
        self.guardrails.append(event)
