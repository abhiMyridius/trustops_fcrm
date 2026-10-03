"""``PipelineTracker`` — accumulates component statuses/errors AND forwards telemetry.

Scope & functionality
----------------------
Two responsibilities that used to live in different places are combined
here, deliberately:

1. **Bookkeeping** (what the original ``PipelineTracker`` did):
   ``success``/``error``/``skipped``/``error_doc``/``degraded`` accumulate
   :class:`~evoq_context.core.contracts.ComponentStatus`/``ErrorDetail``
   entries and compute ``overall_status``. ``stage()``/``astage()`` are
   sync/async context managers that time a stage, call
   ``stage_start``/``stage_end`` on the wrapped observer, and — only when
   the stage itself did not already record its own outcome (checked by
   comparing the component list length before/after) — record a generic
   success/error entry so a stage that has nothing special to say still
   shows up in ``component_statuses``.

2. **Observe-and-forward** (§3.1a): ``LLMClient.complete``/``embed`` and
   ``DocumentPreparer``/``VectorWriter`` are handed *this* tracker as their
   ``observer=`` — not the host's raw observer — so every
   ``LlmCallRecord``/``GuardrailEvent`` they emit passes through
   :meth:`on_llm_call`/:meth:`on_guardrail_event` here first (rolled into
   the running :class:`~evoq_context.core.observer.BuildSummary` this
   tracker owns) and is then forwarded, unchanged, to the real observer the
   host supplied — so a host adapter (or ``LoggingObserver``) still sees
   every call/event exactly as before, and the build gets its
   ``llm_calls``/``llm_tokens_total``/``guardrail_events_by_outcome``
   rollup for free at the end with no separate collection pass.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import asynccontextmanager, contextmanager

from evoq_context.core.contracts import ComponentStatus, ErrorDetail
from evoq_context.core.observer import (
    Attrs,
    AttrValue,
    BuildSummary,
    ContextObserver,
    DocumentRecord,
    GuardrailEvent,
    LlmCallRecord,
    NullObserver,
    StageStatus,
)

logger = logging.getLogger(__name__)


def _ms_since(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


class PipelineTracker:
    """Accumulates component statuses/errors; observes-and-forwards telemetry."""

    def __init__(self, observer: ContextObserver | None = None) -> None:
        """Wrap ``observer`` (defaults to a no-op) and start with empty bookkeeping."""
        self._observer: ContextObserver = observer or NullObserver()
        self.components: list[ComponentStatus] = []
        self.errors: list[ErrorDetail] = []
        #: Set by :meth:`degraded` — a stage succeeded but with a caveat
        #: (e.g. some LLM calls needed a guardrail correction), which pulls
        #: ``overall_status`` down to ``"partial_success"`` even with zero
        #: hard errors.
        self.partial_success: bool = False
        #: Rolled up from every ``on_llm_call``/``on_guardrail_event`` this
        #: tracker observes; ``pipeline/summary.py`` layers doc/stage stats
        #: on top of this same object before emitting it.
        self.summary_counters = BuildSummary(project="", version="", mode="", status="")

    # -- bookkeeping ------------------------------------------------------

    def success(self, name: str, duration_ms: int = 0) -> None:
        """Record ``name`` as a clean success."""
        self.components.append(
            ComponentStatus(name=name, status="success", duration_ms=duration_ms)
        )

    def error(self, name: str, code: str, exc: BaseException, duration_ms: int = 0) -> None:
        """Record ``name`` as failed with a machine-readable ``code``."""
        msg = str(exc)
        self.errors.append(ErrorDetail(stage=name, code=code, message=msg))
        self.components.append(
            ComponentStatus(
                name=name, status="error", message=msg, error_code=code, duration_ms=duration_ms,
            )
        )

    def skipped(self, name: str, message: str = "") -> None:
        """Record ``name`` as intentionally not run."""
        self.components.append(ComponentStatus(name=name, status="skipped", message=message))

    def degraded(self, name: str, code: str, message: str = "") -> None:
        """Record a stage that succeeded but with a caveat — never a hard error."""
        self.partial_success = True
        self.components.append(
            ComponentStatus(name=name, status="degraded", message=message, error_code=code)
        )

    def error_doc(self, stage: str, code: str, doc_id: str, message: str) -> None:
        """Record one document's failure within an otherwise-continuing stage."""
        self.errors.append(ErrorDetail(stage=stage, code=code, message=message, doc_id=doc_id))

    @property
    def has_errors(self) -> bool:
        """Whether any document/stage error has been recorded."""
        return bool(self.errors)

    @property
    def overall_status(self) -> str:
        """``"success"``/``"partial_success"``/``"error"`` for the whole run so far."""
        if not self.errors:
            return "partial_success" if self.partial_success else "success"
        active = [c for c in self.components if c.status != "skipped"]
        if active and all(c.status == "error" for c in active):
            return "error"
        return "partial_success"

    # -- stage timing / telemetry context managers -------------------------

    @contextmanager
    def stage(self, name: str, **attrs: AttrValue) -> Iterator[None]:
        """Sync context manager: time ``name``, observe start/end, record on exit."""
        t0 = time.monotonic()
        n0 = len(self.components)
        handle = self._observer.stage_start(name, dict(attrs))
        try:
            yield
        except Exception as exc:
            duration_ms = _ms_since(t0)
            logger.exception("stage_failed stage=%s", name)
            if len(self.components) == n0:
                self.error(name, f"{name.upper()}_ERROR", exc, duration_ms)
            self._observer.stage_end(handle, status="error", duration_ms=duration_ms, error=exc)
            raise
        else:
            duration_ms = _ms_since(t0)
            if len(self.components) == n0:
                self.success(name, duration_ms)
            status = self._last_status(name)
            self._observer.stage_end(handle, status=status, duration_ms=duration_ms)

    @asynccontextmanager
    async def astage(self, name: str, **attrs: AttrValue) -> AsyncIterator[None]:
        """Async counterpart of :meth:`stage` — see ``pipeline/runner.py::StageRunner``."""
        t0 = time.monotonic()
        n0 = len(self.components)
        handle = self._observer.stage_start(name, dict(attrs))
        try:
            yield
        except Exception as exc:
            duration_ms = _ms_since(t0)
            logger.exception("stage_failed stage=%s", name)
            if len(self.components) == n0:
                self.error(name, f"{name.upper()}_ERROR", exc, duration_ms)
            self._observer.stage_end(handle, status="error", duration_ms=duration_ms, error=exc)
            raise
        else:
            duration_ms = _ms_since(t0)
            if len(self.components) == n0:
                self.success(name, duration_ms)
            status = self._last_status(name)
            self._observer.stage_end(handle, status=status, duration_ms=duration_ms)

    def _last_status(self, name: str) -> StageStatus:
        for component in reversed(self.components):
            if component.name == name:
                status = component.status
                if status in ("success", "error", "skipped", "degraded"):
                    return status  # type: ignore[return-value]
        return "success"

    # -- ContextObserver protocol (observe-and-forward) --------------------

    def stage_start(self, name: str, attrs: Attrs) -> object:
        """Pass through to the wrapped observer (this tracker used as an observer itself)."""
        return self._observer.stage_start(name, attrs)

    def stage_end(
        self,
        handle: object,
        *,
        status: StageStatus,
        duration_ms: int,
        error: BaseException | None = None,
    ) -> None:
        """Pass through to the wrapped observer."""
        self._observer.stage_end(handle, status=status, duration_ms=duration_ms, error=error)

    def document_done(self, record: DocumentRecord) -> None:
        """Pass through to the wrapped observer."""
        self._observer.document_done(record)

    def build_summary(self, summary: BuildSummary) -> None:
        """Pass through to the wrapped observer."""
        self._observer.build_summary(summary)

    def metric(self, name: str, value: float, attrs: Mapping[str, str]) -> None:
        """Pass through to the wrapped observer."""
        self._observer.metric(name, value, attrs)

    def on_llm_call(self, record: LlmCallRecord) -> None:
        """Roll ``record`` into :attr:`summary_counters`, then pass through (§3.1a)."""
        self.summary_counters.record_llm_call(record)
        self._observer.on_llm_call(record)

    def on_guardrail_event(self, event: GuardrailEvent) -> None:
        """Roll ``event`` into :attr:`summary_counters`, then pass through (§3.1a)."""
        self.summary_counters.record_guardrail(event)
        self._observer.on_guardrail_event(event)
