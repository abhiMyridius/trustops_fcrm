"""Tiny OTel helpers shared by ``logging``, ``tracing``, ``errors`` and
``events`` without an import cycle between them. Public callers import
:func:`current_trace_ids` from :mod:`fcrm_common.tracing`.
"""

from __future__ import annotations

from typing import Optional

from opentelemetry import trace


def current_trace_ids() -> tuple[Optional[str], Optional[str]]:
    """``(trace_id, span_id)`` of the active span as lowercase W3C hex
    (32 and 16 characters), or ``(None, None)`` outside any valid span.
    """
    ctx = trace.get_current_span().get_span_context()
    if not ctx.is_valid:
        return None, None
    return format(ctx.trace_id, "032x"), format(ctx.span_id, "016x")
