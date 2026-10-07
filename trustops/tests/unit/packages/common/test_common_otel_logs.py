"""fcrm_common.tracing._OtelLogHandler: stdlib records become OTLP log
records with the same fields, redaction and trace ids as the stdout line.

Uses a real SDK LoggerProvider with an in-memory exporter, and a private
logger, so nothing here touches the process-wide root logger.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from fcrm_common import tracing
from fcrm_common.logging import bound_context
from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.trace import TracerProvider


@pytest.fixture
def pipeline() -> Iterator[tuple[logging.Logger, InMemoryLogRecordExporter]]:
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider()
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    log = logging.getLogger("fcrm.test.otel_logs")
    log.setLevel(logging.DEBUG)
    log.propagate = False
    handler = tracing._OtelLogHandler(provider)
    log.addHandler(handler)
    yield log, exporter
    log.removeHandler(handler)
    provider.shutdown()


def _only(exporter: InMemoryLogRecordExporter):
    [item] = exporter.get_finished_logs()
    return item.log_record


def test_body_severity_and_logger_name_are_exported(pipeline):
    log, exporter = pipeline
    log.warning("band %s needs review", 3)
    record = _only(exporter)
    assert record.body == "band 3 needs review"
    assert record.severity_text == "WARNING"
    assert record.severity_number == SeverityNumber.WARN
    assert record.attributes["logger"] == "fcrm.test.otel_logs"


@pytest.mark.parametrize(
    ("level", "number"),
    [
        (logging.DEBUG, SeverityNumber.DEBUG),
        (logging.INFO, SeverityNumber.INFO),
        (logging.WARNING, SeverityNumber.WARN),
        (logging.ERROR, SeverityNumber.ERROR),
        (logging.CRITICAL, SeverityNumber.FATAL),
    ],
)
def test_severity_follows_the_stdlib_level(level, number):
    assert tracing._severity(level) == number


def test_credential_looking_extras_are_not_exported(pipeline):
    log, exporter = pipeline
    log.info(
        "sign-in",
        extra={
            "user_id": "u-priya",
            "token": "abc",
            "password": "hunter2",
            "request": {"authorization": "Bearer x", "path": "/login"},
        },
    )
    attrs = _only(exporter).attributes
    assert attrs["user_id"] == "u-priya"
    assert "token" not in attrs
    assert "password" not in attrs
    # Redaction is recursive, and the nested dict is shipped as a JSON string.
    assert json.loads(attrs["request"]) == {"path": "/login"}


def test_bound_context_is_exported(pipeline):
    log, exporter = pipeline
    with bound_context(case_id="FCRM-2026-0151", run_id="run_1"):
        log.info("scoring requested")
    attrs = _only(exporter).attributes
    assert attrs["case_id"] == "FCRM-2026-0151"
    assert attrs["run_id"] == "run_1"


def test_trace_and_span_ids_match_the_active_span(pipeline):
    log, exporter = pipeline
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("work") as span:
        log.info("inside the span")
        ctx = span.get_span_context()
    record = _only(exporter)
    assert record.trace_id == ctx.trace_id
    assert record.span_id == ctx.span_id


def test_non_scalar_extras_become_strings(pipeline):
    log, exporter = pipeline
    when = datetime(2026, 10, 7, 12, 0, tzinfo=UTC)
    log.info("types", extra={"when": when, "tags": ["a", "b"], "n": 3, "ok": True})
    attrs = _only(exporter).attributes
    assert attrs["when"] == when.isoformat()
    assert list(attrs["tags"]) == ["a", "b"]
    assert attrs["n"] == 3
    assert attrs["ok"] is True


def test_exception_is_attached(pipeline):
    log, exporter = pipeline
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("failed")
    attrs = _only(exporter).attributes
    assert attrs["exception.type"] == "ValueError"
    assert attrs["exception.message"] == "boom"


def test_opentelemetry_loggers_are_not_exported(pipeline):
    """An exporter failure logs a warning; exporting that would loop."""
    _log, exporter = pipeline
    handler = tracing._OtelLogHandler(LoggerProvider())
    record = logging.LogRecord(
        "opentelemetry.exporter.otlp", logging.WARNING, "x.py", 1, "failed", None, None
    )
    handler.emit(record)
    assert exporter.get_finished_logs() == ()


def test_a_failing_provider_never_raises_into_the_caller(monkeypatch):
    class Broken:
        def get_logger(self, _name):
            raise RuntimeError("exporter is down")

    monkeypatch.setattr(logging, "raiseExceptions", False)
    handler = tracing._OtelLogHandler(Broken())  # type: ignore[arg-type]
    record = logging.LogRecord("app", logging.INFO, "x.py", 1, "hello", None, None)
    handler.emit(record)  # must not raise


def test_a_none_extra_is_left_out_not_sent_as_the_string_none(pipeline):
    # stdout shows ``null`` for it; OTLP has no null attribute, and the string
    # "None" would be a wrong value, so the attribute is simply absent.
    log, exporter = pipeline
    log.info("no user yet", extra={"user_id": None, "case_id": "FCRM-2026-0151"})
    attrs = _only(exporter).attributes
    assert "user_id" not in attrs
    assert attrs["case_id"] == "FCRM-2026-0151"
