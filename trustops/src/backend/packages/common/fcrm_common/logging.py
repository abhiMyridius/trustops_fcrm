"""Structured JSON logging on the stdlib ``logging`` module (no structlog;
design A §1.4, §10.7).

Every line written to stdout is one JSON object with the design A §10.7
keys: ``ts, level, logger, msg, service, version, env, trace_id,
span_id`` plus whatever is bound for the current request or message
(``case_id, run_id, job_id, agent, gate_id, user_id`` ...) and any
``extra=`` fields passed on the call. Keys matching
``password|token|secret|authorization`` are dropped wherever they appear,
so a careless ``extra={"token": ...}`` never reaches the log pipeline.

Binding uses a :class:`contextvars.ContextVar`, so a value bound inside a
request handler or a message consumer is attached to every log line in
that task (and in tasks it spawns) without threading it through calls::

    with bound_context(case_id="FCRM-2026-0151", run_id=run_id):
        logger.info("scoring requested")   # carries case_id and run_id
"""

from __future__ import annotations

import json
import logging
import re
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from types import MappingProxyType
from typing import Any, Optional, TextIO

from fcrm_common._otel import current_trace_ids

REDACT_PATTERN = re.compile(r"password|token|secret|authorization", re.IGNORECASE)

_CONTEXT: ContextVar[Mapping[str, Any]] = ContextVar(
    "fcrm_log_context", default=MappingProxyType({})
)

# Attributes every LogRecord has; anything else on a record came from
# ``extra=`` and is emitted as a field.
_RECORD_ATTRS = frozenset(
    logging.LogRecord("", 0, "", 0, "", None, None).__dict__.keys()
) | {"message", "asctime", "taskName"}

# Keys the formatter owns; a bound or extra value never overwrites them.
_RESERVED = frozenset({"ts", "level", "logger", "msg", "service", "version", "env"})


def bind_context(**values: Any) -> Token[Mapping[str, Any]]:
    """Merge ``values`` into the current logging context.

    Returns the token to pass to :func:`reset_context` to restore the
    previous context; prefer :func:`bound_context` which does that for you.
    A value of ``None`` removes the key.
    """
    merged = {**_CONTEXT.get(), **values}
    return _CONTEXT.set({k: v for k, v in merged.items() if v is not None})


def reset_context(token: Token[Mapping[str, Any]]) -> None:
    """Restore the context that was current before ``bind_context``."""
    _CONTEXT.reset(token)


def clear_context() -> None:
    """Drop every bound value in the current context (start of a request)."""
    _CONTEXT.set({})


def get_context() -> dict[str, Any]:
    """A copy of the values bound in the current context."""
    return dict(_CONTEXT.get())


@contextmanager
def bound_context(**values: Any) -> Iterator[None]:
    """Bind ``values`` for the duration of a ``with`` block."""
    token = bind_context(**values)
    try:
        yield
    finally:
        reset_context(token)


def redact(value: Any) -> Any:
    """Recursively drop mapping keys that look like credentials."""
    if isinstance(value, Mapping):
        return {
            str(k): redact(v)
            for k, v in value.items()
            if not REDACT_PATTERN.search(str(k))
        }
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def _json_default(value: Any) -> str:
    """Fallback for values ``json`` cannot encode (datetimes, enums, ids)."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def record_fields(record: logging.LogRecord) -> dict[str, Any]:
    """The bound context and ``extra=`` fields of ``record``, redacted.

    One definition shared by the stdout JSON line and the OTLP log export, so
    the two never disagree about which fields exist or which are dropped.
    Reserved line keys (``ts``, ``level``, ``logger``, ``msg``, ``service``,
    ``version``, ``env``) are excluded.
    """
    extras = {k: v for k, v in record.__dict__.items() if k not in _RECORD_ATTRS}
    return {
        key: value
        for key, value in redact({**_CONTEXT.get(), **extras}).items()
        if key not in _RESERVED
    }


class JsonFormatter(logging.Formatter):
    """One JSON object per record, with the design A §10.7 field names.

    Args:
        service: ``service`` field on every line (the unit's APP_NAME).
        version: ``version`` field (the unit's package version).
        env: ``env`` field (``local``, ``ci``, ``prod`` ...).
    """

    def __init__(self, service: str, version: str = "0.0.0", env: str = "local"):
        super().__init__()
        self._static = {"service": service, "version": version, "env": env}

    def format(self, record: logging.LogRecord) -> str:
        """Render ``record`` as a single JSON line."""
        ts = datetime.fromtimestamp(record.created, tz=UTC)
        line: dict[str, Any] = {
            "ts": ts.isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            **self._static,
        }
        trace_id, span_id = current_trace_ids()
        if trace_id is not None:
            line["trace_id"] = trace_id
            line["span_id"] = span_id
        line.update(record_fields(record))
        if record.exc_info:
            line["exc_info"] = self.formatException(record.exc_info)
        if record.stack_info:
            line["stack_info"] = self.formatStack(record.stack_info)
        return json.dumps(line, default=_json_default, ensure_ascii=False)


class _FcrmHandler(logging.StreamHandler[TextIO]):
    """Marker subclass so :func:`configure_logging` can find and replace the
    handler it installed earlier (idempotent reconfiguration).
    """


def configure_logging(
    service_name: str,
    level: str = "INFO",
    *,
    version: str = "0.0.0",
    env: str = "local",
    stream: Optional[TextIO] = None,
) -> None:
    """Route every logger in the process to one JSON handler on stdout.

    Safe to call more than once (tests, reloads): the previously installed
    handler is replaced, not duplicated. Uvicorn's loggers are reset to
    propagate to the root so access and error lines come out as JSON too.

    Args:
        service_name: Value of the ``service`` field.
        level: Root level name (``LOG_LEVEL``), e.g. ``"INFO"``.
        version: Value of the ``version`` field.
        env: Value of the ``env`` field (``ENV``).
        stream: Output stream; defaults to ``sys.stdout``.
    """
    root = logging.getLogger()
    for existing in [h for h in root.handlers if isinstance(h, _FcrmHandler)]:
        root.removeHandler(existing)
    handler = _FcrmHandler(stream if stream is not None else sys.stdout)
    handler.setFormatter(JsonFormatter(service_name, version=version, env=env))
    root.addHandler(handler)
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        uv_logger = logging.getLogger(name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True
