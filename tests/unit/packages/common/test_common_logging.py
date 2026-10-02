"""fcrm_common.logging: JSON lines, bound context, redaction (design A §10.7)."""

from __future__ import annotations

import asyncio
import io
import json
import logging

import pytest
from fcrm_common.logging import (
    JsonFormatter,
    bind_context,
    bound_context,
    clear_context,
    configure_logging,
    get_context,
    redact,
    reset_context,
)
from opentelemetry.sdk.trace import TracerProvider


@pytest.fixture
def stream():
    buffer = io.StringIO()
    configure_logging(
        "case-service", "DEBUG", version="0.1.0", env="test", stream=buffer
    )
    clear_context()
    yield buffer
    clear_context()
    root = logging.getLogger()
    for handler in [
        h
        for h in root.handlers
        if isinstance(h, logging.StreamHandler) and h.stream is buffer
    ]:
        root.removeHandler(handler)
    root.setLevel(logging.WARNING)


def lines(buffer: io.StringIO) -> list[dict]:
    return [json.loads(line) for line in buffer.getvalue().splitlines() if line]


def test_configure_logging_writes_one_json_object_per_line(stream):
    logging.getLogger("fcrm.test").info("hello %s", "world")
    [line] = lines(stream)
    assert line["msg"] == "hello world"
    assert line["level"] == "INFO"
    assert line["logger"] == "fcrm.test"
    assert line["service"] == "case-service"
    assert line["version"] == "0.1.0"
    assert line["env"] == "test"
    assert line["ts"].endswith("Z")


def test_configure_logging_is_idempotent(stream):
    configure_logging("case-service", "INFO", stream=stream)
    logging.getLogger("fcrm.test").info("once")
    assert len(lines(stream)) == 1


def test_bound_context_attaches_to_every_line_and_resets(stream):
    log = logging.getLogger("fcrm.test")
    with bound_context(case_id="FCRM-2026-0151", run_id="run_1"):
        log.info("inside")
        with bound_context(job_id="job_1"):
            log.info("nested")
        log.info("after nested")
    log.info("outside")
    inside, nested, after, outside = lines(stream)
    assert inside["case_id"] == "FCRM-2026-0151"
    assert inside["run_id"] == "run_1"
    assert nested["job_id"] == "job_1"
    assert "job_id" not in after
    assert "case_id" not in outside


def test_bind_context_none_removes_key():
    clear_context()
    token = bind_context(case_id="FCRM-2026-0151")
    inner = bind_context(case_id=None)
    assert "case_id" not in get_context()
    reset_context(inner)
    assert get_context() == {"case_id": "FCRM-2026-0151"}
    reset_context(token)
    assert get_context() == {}


async def test_bound_context_is_isolated_between_tasks():
    clear_context()

    async def worker(case_id: str) -> str:
        with bound_context(case_id=case_id):
            await asyncio.sleep(0)
            return str(get_context()["case_id"])

    assert await asyncio.gather(worker("A"), worker("B")) == ["A", "B"]
    assert get_context() == {}


def test_extra_fields_are_emitted_and_secrets_dropped(stream):
    logging.getLogger("fcrm.test").info(
        "call",
        extra={
            "dependency": "opa",
            "http": {"status": 200, "authorization": "Bearer x"},
            "token": "abc",
            "password": "p",
        },
    )
    [line] = lines(stream)
    assert line["dependency"] == "opa"
    assert line["http"] == {"status": 200}
    assert "token" not in line
    assert "password" not in line


def test_redact_handles_nested_lists():
    assert redact({"a": [{"secret": 1, "b": 2}], "API_TOKEN": "x"}) == {"a": [{"b": 2}]}


def test_reserved_fields_cannot_be_overwritten(stream):
    with bound_context(service="spoofed", msg="spoofed"):
        logging.getLogger("fcrm.test").info("real")
    [line] = lines(stream)
    assert line["service"] == "case-service"
    assert line["msg"] == "real"


def test_trace_ids_injected_inside_a_span(stream):
    tracer = TracerProvider().get_tracer("test")
    with tracer.start_as_current_span("op") as span:
        logging.getLogger("fcrm.test").info("traced")
        ctx = span.get_span_context()
    [line] = lines(stream)
    assert line["trace_id"] == format(ctx.trace_id, "032x")
    assert line["span_id"] == format(ctx.span_id, "016x")


def test_exception_is_formatted_into_exc_info(stream):
    try:
        raise ValueError("bad value")
    except ValueError:
        logging.getLogger("fcrm.test").exception("failed")
    [line] = lines(stream)
    assert "ValueError: bad value" in line["exc_info"]


def test_json_formatter_never_raises_on_unserialisable_extra():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "m", None, None)
    record.obj = object()
    out = json.loads(JsonFormatter("svc").format(record))
    assert out["obj"].startswith("<object")
