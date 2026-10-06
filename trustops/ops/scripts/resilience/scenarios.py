"""The building blocks of the drills: scratch cases, uploads, waiting on the
audit chain, and the messages the DLQ drill injects.

A drill reads as: build a situation with these, inject one failure, wait for
the observable, assert. The checks themselves are in :mod:`resilience.chain`.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final, Optional

from resilience.broker import Broker, QueueStat
from resilience.errors import CommandError, GatewayError
from resilience.gateway import GatewayClient, Reply
from resilience.polling import poll_until

logger = logging.getLogger(__name__)

#: Every case a drill raises carries this in its title, so a leftover is easy
#: to find and never mistaken for demo data.
SCRATCH_PREFIX: Final = "[resilience-drill]"
PROJECT_ROOT: Final = Path(__file__).resolve().parents[3]
FIXTURES_DIR: Final = PROJECT_ROOT / "evals" / "fixtures" / "documents"
#: The smallest of the three demo documents (5 KB): the cheapest honest
#: trigger of one intake agent run.
SMALL_DOCUMENT: Final = "vendor_DD_scope_letter_2026.pdf"
DISCARD_REASON: Final = "resilience drill scratch case, safe to discard"
IN_FLIGHT_QUEUE: Final = "agent-worker.run"
#: The queues whose emptiness means "the harness has nothing left to do".
WORK_QUEUES: Final[tuple[str, ...]] = (
    "caseflow.events",
    "caseflow.agent-results",
    "caseflow.steps",
    "agent-worker.run",
    "ingestion-worker.run",
    "context-worker.run",
)


def utc_stamp(now: Optional[datetime] = None) -> str:
    """``20261006T120501Z``: a compact UTC stamp for titles and file names."""
    return (now or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")


def create_scratch_case(gateway: GatewayClient, label: str) -> str:
    """Raise a draft case as the PO and return its id.

    Raises:
        GatewayError: the PO's write was refused.
    """
    title = f"{SCRATCH_PREFIX} {label} {utc_stamp()}"
    body = gateway.post("po", "/cases", json={"title": title}).require(201).body
    if not isinstance(body, Mapping) or "id" not in body:
        raise GatewayError("POST /cases did not answer a case")
    return str(body["id"])


def discard_scratch_case(gateway: GatewayClient, case_id: str) -> bool:
    """Discard a scratch draft so it leaves the PO's lists; best effort.

    A case that has been submitted cannot be discarded, and its audit chain is
    append-only either way; the return value says whether it went.
    """
    reply = gateway.post(
        "po", f"/cases/{case_id}/discard", json={"reason": DISCARD_REASON}
    )
    if reply.status not in (200, 204):
        logger.warning(
            "scratch case %s was not discarded (HTTP %s)", case_id, reply.status
        )
    return reply.status in (200, 204)


def upload_document(
    gateway: GatewayClient, case_id: str, name: str = SMALL_DOCUMENT
) -> None:
    """Upload one committed demo document to ``case_id`` as the PO.

    Raises:
        GatewayError: the upload was refused.
    """
    path = FIXTURES_DIR / name
    files = {"files": (name, path.read_bytes(), "application/pdf")}
    gateway.post(
        "po", f"/cases/{case_id}/documents", files=files, timeout_s=90.0
    ).require(201)


def audit_records(gateway: GatewayClient, case_id: str) -> list[dict[str, Any]]:
    """Every audit record of ``case_id`` in chain order (as the analyst)."""
    rows = gateway.pages("analyst", f"/audit/records/{case_id}")
    return [dict(row) for row in rows]


def record_types(records: list[dict[str, Any]]) -> list[str]:
    """The ``type`` of each record, in order."""
    return [str(row.get("type")) for row in records]


def wait_for_record(
    gateway: GatewayClient, case_id: str, record_type: str, *, timeout_s: float
) -> dict[str, Any]:
    """Block until the case's chain holds a record of ``record_type`` and
    return the first one.

    Raises:
        PollTimeoutError: none appeared in time.
    """

    def probe() -> Optional[dict[str, Any]]:
        for row in audit_records(gateway, case_id):
            if row.get("type") == record_type:
                return row
        return None

    return poll_until(
        probe,
        what=f"{record_type} in the audit chain of {case_id}",
        timeout_s=timeout_s,
        interval_s=2.0,
        tolerate=(GatewayError,),
    )


def run_and_step_ids(record: Mapping[str, Any]) -> tuple[str, str]:
    """``(run_id, step_id)`` a step-written record carries in its ``data``.

    Raises:
        GatewayError: the record has neither (it was not written by a step).
    """
    data = record.get("data")
    data = data if isinstance(data, Mapping) else {}
    run_id, step_id = data.get("run_id"), data.get("step_id")
    if not isinstance(run_id, str) or not isinstance(step_id, str):
        raise GatewayError(f"record {record.get('type')} has no run_id/step_id")
    return run_id, step_id


def verify_chain(gateway: GatewayClient, case_id: str) -> Mapping[str, Any]:
    """``GET audit/verify/{case_id}``: ``{record_count, ok, first_break_seq}``."""
    body = gateway.get("analyst", f"/audit/verify/{case_id}").require(200).body
    if not isinstance(body, Mapping):
        raise GatewayError("audit verify did not answer an object")
    return body


def wait_for_job_in_flight(broker: Broker, *, timeout_s: float) -> QueueStat:
    """Block until an agent job is being worked on: ``agent-worker.run`` holds a
    delivered, unacknowledged message (the model call is running).

    Raises:
        PollTimeoutError: no job was in flight in time.
    """

    def probe() -> Optional[QueueStat]:
        stat = broker.queues().get(IN_FLIGHT_QUEUE)
        return stat if stat is not None and stat.unacked >= 1 else None

    return poll_until(
        probe,
        what=f"an agent job in flight on {IN_FLIGHT_QUEUE}",
        timeout_s=timeout_s,
        interval_s=0.4,
    )


def step_envelope(
    case_id: str,
    run_id: str,
    step_id: str,
    step: str,
    *,
    event_id: str,
    now: Optional[datetime] = None,
) -> bytes:
    """The body of a ``harness.step`` message for ``step_id`` of ``run_id``.

    With the id of a step that already finished it is a valid message whose
    handler does nothing, which is what the replay drill needs.
    """
    return _envelope(
        "harness.step",
        case_id,
        run_id,
        event_id,
        now,
        {
            "step_id": step_id,
            "step": step,
            "case_id": case_id,
            "run_id": run_id,
            "args": {},
        },
    )


def _envelope(
    event_type: str,
    case_id: str,
    run_id: str,
    event_id: str,
    now: Optional[datetime],
    payload: Mapping[str, Any],
) -> bytes:
    """An event envelope as JSON bytes (``fcrm_contracts`` ``Envelope``)."""
    envelope = {
        "event_id": event_id,
        "type": event_type,
        "version": 1,
        "case_id": case_id,
        "run_id": run_id,
        "correlation_id": run_id,
        "occurred_at": (now or datetime.now(UTC)).isoformat(),
        "payload": dict(payload),
    }
    return json.dumps(envelope).encode("utf-8")


def poison_step_envelope(
    case_id: str,
    run_id: str,
    *,
    event_id: str,
    now: Optional[datetime] = None,
) -> bytes:
    """A well-formed envelope whose payload is not a step (an unknown ``step``
    kind): the consumer's handler rejects it every time, so it is retried
    and then parked.
    """
    return _envelope(
        "harness.step",
        case_id,
        run_id,
        event_id,
        now,
        {
            "step_id": f"step_{event_id}",
            "step": "no.such.step",
            "case_id": case_id,
            "run_id": run_id,
            "args": {},
        },
    )


def agent_done_envelope(
    case_id: str,
    run_id: str,
    job_id: str,
    agent: str,
    *,
    event_id: str,
    now: Optional[datetime] = None,
) -> bytes:
    """The body of an ``agent.done`` for ``job_id`` with nothing accepted: what
    a second run of a redelivered job would publish (a fresh event id, the same
    job).
    """
    return _envelope(
        "agent.done",
        case_id,
        run_id,
        event_id,
        now,
        {
            "job_id": job_id,
            "case_id": case_id,
            "run_id": run_id,
            "agent": agent,
            "output": {
                "accepted": [],
                "dropped": [],
                "notes_for_analyst": [],
                "guard_summary": {
                    "claims_total": 0,
                    "claims_with_source": 0,
                    "dropped": 0,
                },
                "attempts": 1,
            },
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "cache_write_tokens": 0,
            },
            "model": "resilience-drill",
            "attempt": 1,
        },
    )


def agent_run_envelope(
    case_id: str,
    run_id: str,
    job_id: str,
    pins: Mapping[str, Any],
    *,
    agent: str = "intake",
    event_id: str,
    now: Optional[datetime] = None,
) -> bytes:
    """The body of an ``agent.run`` for ``agent`` with no ``refs``. For
    ``intake`` that is a job H2 cannot build a context pack for (it needs a
    ``doc_id``), so it ends in ``agent.failed`` at once, with no model call.
    """
    return _envelope(
        "agent.run",
        case_id,
        run_id,
        event_id,
        now,
        {
            "job_id": job_id,
            "case_id": case_id,
            "run_id": run_id,
            "agent": agent,
            "tier": "cheap",
            "step_key": "resilience-drill",
            "attempt": 1,
            "scope": None,
            "changed_fields": [],
            "pins": dict(pins),
            "refs": {},
        },
    )


def new_dead_letters(
    before: Mapping[str, int], after: Mapping[str, int]
) -> dict[str, int]:
    """The dead-letter queues that hold more now than ``before``, with how
    many more. A queue that is new counts from zero.
    """
    return {
        name: depth - before.get(name, 0)
        for name, depth in after.items()
        if depth > before.get(name, 0)
    }


def try_create_scratch_case(
    gateway: GatewayClient, label: str
) -> tuple[Reply, Optional[str]]:
    """Raise a draft case and report the reply as it came, without insisting
    on success: ``(reply, case id or None)``. The outage drills use it to see
    what a write gets while a dependency is down.
    """
    title = f"{SCRATCH_PREFIX} {label} {utc_stamp()}"
    reply = gateway.post("po", "/cases", json={"title": title})
    body = reply.body
    if reply.status == 201 and isinstance(body, Mapping) and "id" in body:
        return reply, str(body["id"])
    return reply, None


def case_titles(gateway: GatewayClient) -> list[str]:
    """The titles of the PO's cases (all pages)."""
    rows = gateway.pages("po", "/cases")
    return [str(row.get("title")) for row in rows if isinstance(row, Mapping)]


def wait_for_idle(
    broker: Broker,
    *,
    timeout_s: float,
    queues: Sequence[str] = WORK_QUEUES,
    settle_polls: int = 3,
    interval_s: float = 2.0,
) -> None:
    """Block until every queue in ``queues`` is empty (ready and in flight) on
    ``settle_polls`` polls in a row: nothing is being worked on and nothing is
    waiting.

    Raises:
        PollTimeoutError: the queues did not drain in time.
    """
    quiet = {"polls": 0}

    def probe() -> Optional[bool]:
        stats = broker.queues()
        busy = [q for q in queues if q in stats and stats[q].depth > 0]
        quiet["polls"] = 0 if busy else quiet["polls"] + 1
        return True if quiet["polls"] >= settle_polls else None

    poll_until(
        probe,
        what=f"{', '.join(queues)} to be empty",
        timeout_s=timeout_s,
        interval_s=interval_s,
        tolerate=(CommandError,),
    )
