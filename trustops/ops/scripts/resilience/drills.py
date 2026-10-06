"""The drills as flows: build a situation, inject one failure, wait for the
observable, and hand back what was seen.

The test modules in ``tests/resilience`` only assert on these outcomes, so the
property each drill proves (``docs/architecture/resilience.md``) is stated in
one place and the flows themselves are plain functions with no pytest in them.
Every flow that stops or kills a container puts it back in a ``finally`` and
waits for readiness, so a failed drill still leaves the stack healthy.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Final, Optional

from resilience.broker import DLQ_SUFFIX, RETRY_SUFFIX
from resilience.chain import chain_problems, stream_problems
from resilience.docker import ContainerState, DockerCli
from resilience.errors import (
    BrokerAgentError,
    CommandError,
    DrillError,
    PollTimeoutError,
)
from resilience.gateway import Reply
from resilience.polling import poll_until
from resilience.scenarios import (
    IN_FLIGHT_QUEUE,
    PROJECT_ROOT,
    agent_done_envelope,
    agent_run_envelope,
    audit_records,
    baseline_dead_letters,
    case_titles,
    create_scratch_case,
    deliveries,
    new_dead_letters,
    poison_step_envelope,
    rfc3339,
    run_and_step_ids,
    step_envelope,
    try_create_scratch_case,
    upload_document,
    utc_stamp,
    verify_chain,
    wait_for_idle,
    wait_for_job_in_flight,
    wait_for_record,
)
from resilience.shell import CommandResult
from resilience.stack import Stack

logger = logging.getLogger(__name__)

#: Time for H1 to ingest a document and dispatch the intake job.
IN_FLIGHT_TIMEOUT_S: Final = 150.0
#: Time for a whole intake run (ingest, one model call, publish) after a restart.
COMPLETE_TIMEOUT_S: Final = 300.0
READY_TIMEOUT_S: Final = 240.0
RECORD_TIMEOUT_S: Final = 120.0
IDLE_TIMEOUT_S: Final = 120.0
#: The workers' ``stop_grace_period`` in compose; the api's is 30 s.
HARNESS_GRACE_S: Final = 70
API_GRACE_S: Final = 30
#: How long a message sits in a ``*.retry`` queue (``x-message-ttl``, 30 s).
RETRY_DELAY_S: Final = 30.0
#: Attempts a ``caseflow.steps`` message gets: ``STEP_MAX_ATTEMPTS`` of
#: ``fcrm_caseflow.consumers`` (a unit test keeps the two equal).
STEP_ATTEMPTS: Final = 8
#: A message that fails on every attempt reaches the DLQ after this long, plus slack.
RETRY_CYCLE_TIMEOUT_S: Final = STEP_ATTEMPTS * RETRY_DELAY_S + 90.0
#: A request that gets no answer within this is a hang, not a clean failure.
CLEAN_FAILURE_LIMIT_S: Final = 40.0
STEPS_QUEUE: Final = "caseflow.steps"
STEPS_DLQ: Final = f"{STEPS_QUEUE}{DLQ_SUFFIX}"
STEPS_RETRY: Final = f"{STEPS_QUEUE}{RETRY_SUFFIX}"

#: Types an intake-only case with one document holds exactly once.
INTAKE_ONCE_TYPES: Final[tuple[str, ...]] = (
    "case.created",
    "workflow.started",
    "case.document_stored",
    "document.stored",
    "ingest.done",
    "guard.input",
    "agent.done.intake",
    "proposal.extraction",
    "proposal.category",
)
CREATED_ONCE_TYPES: Final[tuple[str, ...]] = ("case.created", "workflow.started")


# --------------------------------------------------------------------- shared


@dataclass(frozen=True)
class Probe:
    """One request made while a dependency was down, as the caller saw it.

    Attributes:
        name: What was asked.
        status: The HTTP status (``0``: no answer at all, a hang or refusal).
        problem: Whether the answer was ``application/problem+json``.
        seconds: How long the caller waited.
        detail: The transport error, or the problem's ``code``.
    """

    name: str
    status: int
    problem: bool
    seconds: float
    detail: str


def probe_of(name: str, reply: Reply) -> Probe:
    """A :class:`Probe` from a :class:`Reply`."""
    code = ""
    if isinstance(reply.body, Mapping):
        code = str(reply.body.get("code", ""))
    return Probe(
        name=name,
        status=reply.status,
        problem=reply.is_problem,
        seconds=reply.elapsed_s,
        detail=reply.error or code,
    )


def is_clean_failure(probe: Probe) -> bool:
    """Whether a request that failed failed *cleanly*: a gateway or upstream
    status (502/503/504) that came back within :data:`CLEAN_FAILURE_LIMIT_S`.
    """
    return probe.status in (502, 503, 504) and probe.seconds < CLEAN_FAILURE_LIMIT_S


def restore(docker: DockerCli, *services: str) -> None:
    """Start any of ``services`` that is not running and wait for the Python
    processes among them to be ready: the ``finally`` of every outage drill.
    """
    for service in services:
        try:
            docker.ensure_running(service)
        except CommandError:
            logger.exception("could not bring %s back", service)
    for service in ("api", "harness"):
        try:
            docker.wait_ready(service, timeout_s=READY_TIMEOUT_S)
        except PollTimeoutError:
            logger.exception("%s did not become ready after the drill", service)


def settled_chain(
    stack: Stack, case_id: str, once_types: tuple[str, ...]
) -> tuple[list[dict[str, Any]], list[str], Mapping[str, Any]]:
    """``(records, problems, verify)`` of a case's chain right now."""
    records = audit_records(stack.gateway, case_id)
    problems = chain_problems(records, once_types=once_types)
    return records, problems, verify_chain(stack.gateway, case_id)


def case_ledger(stack: Stack, case_id: str) -> Optional[dict[str, Any]]:
    """The case's token ledger (``case_budgets/<case_id>`` in H1's database):
    ``cap``, ``spent``, ``reserved`` and ``jobs`` by job id. ``None`` when the
    case has none.
    """
    rows = stack.mongo.find("fcrm_harness", "case_budgets", {"_id": case_id}, limit=1)
    return rows[0] if rows else None


def names_message(shown: Mapping[str, Any], wanted: str) -> bool:
    """Whether a peeked message is the one published as ``wanted``: by its AMQP
    message id or by the ``event_id`` of its envelope (a retry hop on an adapter
    that did not carry the AMQP properties gives the message a made-up id).
    """
    return wanted in (shown.get("message_id"), shown.get("event_id"))


#: Run in the harness: does its broker adapter carry the fix (see below)?
_ADAPTER_PROBE = "import fcrm_common.broker as b; print(hasattr(b, 'delivery_headers'))"


def adapter_fix_deployed(stack: Stack) -> bool:
    """Whether the running harness has the broker adapter that decodes nothing
    and carries a message's AMQP properties over a retry (``delivery_headers``
    in ``fcrm_common.broker``).

    Without it a message whose body is not JSON but whose ``content_type`` says
    ``application/json`` is never acknowledged, so it stays on the queue holding
    a prefetch slot and comes back after every restart: the poison drill must
    not plant one in a stack that cannot get rid of it.
    """
    try:
        result = stack.docker.exec(
            "harness", ["python", "-c", _ADAPTER_PROBE], timeout_s=30.0
        )
    except CommandError:
        return False
    return result.stdout.strip() == "True"


def run_make(
    stack: Stack, args: list[str], *, timeout_s: float = 180.0
) -> CommandResult:
    """``make <args>`` from the project root, without raising on a failure."""
    return stack.runner.run(
        [
            "make",
            "-C",
            str(PROJECT_ROOT),
            f"DLQ_PROJECT={stack.docker.project}",
            *args,
        ],
        timeout_s=timeout_s,
        check=False,
    )


# ------------------------------------------------------------ D1: harness kill


@dataclass(frozen=True)
class MidRunOutcome:
    """What a harness interrupted mid-run left behind.

    Attributes:
        case_id: The scratch case.
        mode: ``graceful`` (SIGTERM, drain) or ``kill`` (SIGKILL).
        in_flight: The short message id of the job caught running (from the
            harness log).
        restarted: The harness's new run.
        redelivered: Whether the new process was handed the job again.
        interrupt_s: How long the restart took.
        complete_s: From the interruption to the run's ``proposal.extraction``.
        records: The case's audit chain after the run completed.
        problems: Sequence and duplicate problems in it.
        verify: ``GET audit/verify`` of the case.
        new_dead_letters: Dead-letter queues that grew during the drill.
        ledger: The case's token ledger once the run finished.
    """

    case_id: str
    mode: str
    in_flight: str
    restarted: ContainerState
    redelivered: bool
    interrupt_s: float
    complete_s: float
    records: list[dict[str, Any]]
    problems: list[str]
    verify: Mapping[str, Any]
    new_dead_letters: dict[str, int]
    ledger: Optional[dict[str, Any]]


def interrupt(
    docker: DockerCli, service: str, mode: str, previous_started_at: str
) -> ContainerState:
    """Restart ``service`` the hard way (``kill``) or the polite way
    (``graceful``) and return its new run.

    Raises:
        ValueError: ``mode`` is neither.
        PollTimeoutError: no new run started.
    """
    if mode == "graceful":
        docker.restart(service, grace_s=HARNESS_GRACE_S)
    elif mode == "kill":
        docker.kill(service)
        try:
            return docker.wait_restarted(
                service, previous_started_at=previous_started_at, timeout_s=30
            )
        except PollTimeoutError:
            docker.ensure_running(service)
    else:
        raise ValueError(f"unknown interruption {mode!r}")
    return docker.wait_restarted(
        service, previous_started_at=previous_started_at, timeout_s=120
    )


def interrupt_harness_midrun(stack: Stack, case_id: str, *, mode: str) -> MidRunOutcome:
    """Upload a document, catch the intake job in flight, interrupt the
    harness, and let the run finish.
    """
    docker, broker, gateway = stack.docker, stack.broker, stack.gateway
    dead_before = baseline_dead_letters(broker)
    previous = docker.state("harness").started_at
    since = rfc3339(datetime.now(UTC) - timedelta(seconds=5))
    upload_document(gateway, case_id)
    in_flight = wait_for_job_in_flight(
        docker, since=since, timeout_s=IN_FLIGHT_TIMEOUT_S
    )
    began = time.monotonic()
    try:
        restarted = interrupt(docker, "harness", mode, previous)
    finally:
        restore(docker, "harness")
    interrupt_s = time.monotonic() - began
    wait_for_record(
        gateway, case_id, "proposal.extraction", timeout_s=COMPLETE_TIMEOUT_S
    )
    complete_s = time.monotonic() - began
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    new_run_log = docker.logs_since("harness", restarted.started_at)
    delivered_again, _ = deliveries(new_run_log, IN_FLIGHT_QUEUE)
    records, problems, verify = settled_chain(stack, case_id, INTAKE_ONCE_TYPES)
    return MidRunOutcome(
        case_id=case_id,
        mode=mode,
        in_flight=in_flight,
        restarted=restarted,
        redelivered=in_flight in delivered_again,
        interrupt_s=interrupt_s,
        complete_s=complete_s,
        records=records,
        problems=problems,
        verify=verify,
        new_dead_letters=new_dead_letters(dead_before, broker.dead_letter_depths()),
        ledger=case_ledger(stack, case_id),
    )


@dataclass(frozen=True)
class DuplicateDoneOutcome:
    """What a second ``agent.done`` for a finished job did.

    Attributes:
        case_id: The scratch case.
        job_id: The intake job the duplicate names.
        new_records: Audit records that appeared after it was published.
        problems: Sequence and duplicate problems in the chain afterwards.
        verify: ``GET audit/verify`` of the case.
        new_dead_letters: Dead-letter queues that grew.
        ledger_before: The case's token ledger before the duplicate.
        ledger_after: The same after it (a settled job settles once).
    """

    case_id: str
    job_id: str
    new_records: list[dict[str, Any]]
    problems: list[str]
    verify: Mapping[str, Any]
    new_dead_letters: dict[str, int]
    ledger_before: Optional[dict[str, Any]]
    ledger_after: Optional[dict[str, Any]]


def inject_duplicate_agent_done(stack: Stack, case_id: str) -> DuplicateDoneOutcome:
    """Finish an intake run, then publish a second ``agent.done`` for the same
    job under a fresh event id, which is what a redelivered job's second run
    would publish after the first had already reported.
    """
    broker, gateway = stack.broker, stack.gateway
    upload_document(gateway, case_id)
    wait_for_record(
        gateway, case_id, "proposal.extraction", timeout_s=COMPLETE_TIMEOUT_S
    )
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    dead_before = baseline_dead_letters(broker)
    before = audit_records(gateway, case_id)
    ledger_before = case_ledger(stack, case_id)
    permit = next(
        r
        for r in before
        if r["type"] == "guard.permit" and r["data"].get("agent") == "intake"
    )
    job_id = str(permit["data"]["job_id"])
    run_id, _ = run_and_step_ids(
        next(r for r in before if r["type"] == "workflow.started")
    )
    event_id = f"evt_drill_dup_{utc_stamp()}"
    broker.publish(
        "agent.done",
        agent_done_envelope(case_id, run_id, job_id, "intake", event_id=event_id),
        exchange="agent.jobs",
        message_id=event_id,
        message_type="agent.done",
        headers={"x-attempt": 0},
    )
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    time.sleep(10.0)  # the step the duplicate would enqueue runs after the queues drain
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    after, problems, verify = settled_chain(stack, case_id, INTAKE_ONCE_TYPES)
    return DuplicateDoneOutcome(
        case_id=case_id,
        job_id=job_id,
        new_records=after[len(before) :],
        problems=problems,
        verify=verify,
        new_dead_letters=new_dead_letters(dead_before, broker.dead_letter_depths()),
        ledger_before=ledger_before,
        ledger_after=case_ledger(stack, case_id),
    )


# ------------------------------------------------------------ D2: broker outage


@dataclass(frozen=True)
class BrokerOutageOutcome:
    """What a RabbitMQ stop and start did.

    Attributes:
        write: The PO's write while the broker was down.
        api_ready_during: ``/readyz`` status of the api while it was down.
        harness_ready_during: The same for the harness.
        recovery_s: From the broker's start until every work queue had a
            consumer again.
        outage_case: The case raised while the broker was down.
        after_case: A case raised after it came back.
        outage_records: The outage case's chain once H1 had caught up.
        after_records: The after case's chain.
        problems: Sequence and duplicate problems in both chains.
        verifies: ``audit/verify`` of both cases.
        new_dead_letters: Dead-letter queues that grew.
    """

    write: Reply
    api_ready_during: int
    harness_ready_during: int
    recovery_s: float
    outage_case: Optional[str]
    after_case: str
    outage_records: list[dict[str, Any]]
    after_records: list[dict[str, Any]]
    problems: list[str]
    verifies: list[Mapping[str, Any]]
    new_dead_letters: dict[str, int]


def wait_for_consumers(stack: Stack, *, timeout_s: float) -> None:
    """Block until every work queue has a consumer attached.

    Raises:
        PollTimeoutError: one still has none.
    """

    def probe() -> Optional[bool]:
        return True if not stack.broker.consumerless() else None

    poll_until(
        probe,
        what="a consumer on every work queue",
        timeout_s=timeout_s,
        interval_s=2.0,
        tolerate=(CommandError, BrokerAgentError),
    )


def outage_of_rabbitmq(stack: Stack, *, hold_s: float = 8.0) -> BrokerOutageOutcome:
    """Stop RabbitMQ, write through the PO while it is down, start it, and
    watch the harness and the api catch up.
    """
    docker, broker, gateway = stack.docker, stack.broker, stack.gateway
    gateway.login("po")
    gateway.login("analyst")
    dead_before = baseline_dead_letters(broker)
    docker.stop("rabbitmq", grace_s=30)
    try:
        write, outage_case = try_create_scratch_case(gateway, "broker down")
        api_ready = docker.readyz("api").status_code
        harness_ready = docker.readyz("harness").status_code
        time.sleep(hold_s)
    finally:
        docker.start("rabbitmq")
    began = time.monotonic()
    wait_for_consumers(stack, timeout_s=READY_TIMEOUT_S)
    recovery_s = time.monotonic() - began
    restore(docker)
    outage_records: list[dict[str, Any]] = []
    if outage_case is not None:
        wait_for_record(
            gateway, outage_case, "workflow.started", timeout_s=RECORD_TIMEOUT_S
        )
        outage_records = audit_records(gateway, outage_case)
    after_case = create_scratch_case(gateway, "broker back")
    wait_for_record(gateway, after_case, "workflow.started", timeout_s=RECORD_TIMEOUT_S)
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    problems: list[str] = []
    verifies: list[Mapping[str, Any]] = []
    for case_id in filter(None, (outage_case, after_case)):
        _, found, verify = settled_chain(stack, case_id, CREATED_ONCE_TYPES)
        problems.extend(found)
        verifies.append(verify)
    return BrokerOutageOutcome(
        write=write,
        api_ready_during=api_ready,
        harness_ready_during=harness_ready,
        recovery_s=recovery_s,
        outage_case=outage_case,
        after_case=after_case,
        outage_records=outage_records,
        after_records=audit_records(gateway, after_case),
        problems=problems,
        verifies=verifies,
        new_dead_letters=new_dead_letters(dead_before, broker.dead_letter_depths()),
    )


# ------------------------------------------------------------- D3: mongo outage


@dataclass(frozen=True)
class MongoOutageOutcome:
    """What a Mongo stop and start did.

    Attributes:
        probes: Requests made while Mongo was down.
        api_ready_during: ``/readyz`` status of the api while it was down.
        harness_ready_during: The same for the harness.
        recovery_s: From Mongo's start until the api and harness were ready.
        failed_write_title: Title of the case the failed write tried to raise.
        failed_write_visible: Whether a case with that title exists now (half
            applied state).
        survivor: The case raised before the outage.
        survivor_status: ``GET`` of it after recovery.
        after_case: A case raised after recovery.
        after_records: Its chain once H1 had caught up.
        problems: Sequence and duplicate problems in the chains.
        verifies: ``audit/verify`` of the survivor and the after case.
        new_dead_letters: Dead-letter queues that grew.
    """

    probes: list[Probe]
    api_ready_during: int
    harness_ready_during: int
    recovery_s: float
    failed_write_title: str
    failed_write_visible: bool
    survivor: str
    survivor_status: int
    after_case: str
    after_records: list[dict[str, Any]]
    problems: list[str]
    verifies: list[Mapping[str, Any]]
    new_dead_letters: dict[str, int]


def probe_while_down(stack: Stack, title: str) -> list[Probe]:
    """The requests every outage drill makes: a read and a write as the PO, the
    identity ``me`` and the analyst queue as the analyst, each with a long
    client timeout so a hang shows as a slow answer, not a client error.
    """
    gateway = stack.gateway
    replies = (
        ("GET /cases (PO read)", gateway.call("po", "GET", "/cases", timeout_s=45.0)),
        (
            "POST /cases (PO write)",
            gateway.call("po", "POST", "/cases", json={"title": title}, timeout_s=45.0),
        ),
        ("GET /auth/me", gateway.call("analyst", "GET", "/auth/me", timeout_s=45.0)),
        (
            "GET /queue (analyst read)",
            gateway.call("analyst", "GET", "/queue", timeout_s=45.0),
        ),
    )
    return [probe_of(name, reply) for name, reply in replies]


def outage_of_mongo(stack: Stack, *, hold_s: float = 5.0) -> MongoOutageOutcome:
    """Stop Mongo, make requests while it is down, start it, and check that
    nothing was half applied and that writes work again.
    """
    docker, broker, gateway = stack.docker, stack.broker, stack.gateway
    gateway.login("po")
    gateway.login("analyst")
    dead_before = baseline_dead_letters(broker)
    survivor = create_scratch_case(gateway, "mongo survivor")
    wait_for_record(gateway, survivor, "workflow.started", timeout_s=RECORD_TIMEOUT_S)
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    title = f"[resilience-drill] mongo down {utc_stamp()}"
    docker.stop("mongo", grace_s=30)
    try:
        probes = probe_while_down(stack, title)
        api_ready = docker.readyz("api").status_code
        harness_ready = docker.readyz("harness").status_code
        time.sleep(hold_s)
    finally:
        docker.start("mongo")
    began = time.monotonic()
    try:
        poll_until(
            lambda: True if docker.state("mongo").healthy else None,
            what="mongo to be healthy again",
            timeout_s=READY_TIMEOUT_S,
            interval_s=2.0,
            tolerate=(CommandError,),
        )
    finally:
        restore(docker, "mongo")
    recovery_s = time.monotonic() - began
    visible = any(t == title for t in case_titles(gateway))
    survivor_status = gateway.get("po", f"/cases/{survivor}").status
    after_case = create_scratch_case(gateway, "mongo back")
    wait_for_record(gateway, after_case, "workflow.started", timeout_s=RECORD_TIMEOUT_S)
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    problems: list[str] = []
    verifies: list[Mapping[str, Any]] = []
    for case_id in (survivor, after_case):
        _, found, verify = settled_chain(stack, case_id, CREATED_ONCE_TYPES)
        problems.extend(found)
        verifies.append(verify)
    return MongoOutageOutcome(
        probes=probes,
        api_ready_during=api_ready,
        harness_ready_during=harness_ready,
        recovery_s=recovery_s,
        failed_write_title=title,
        failed_write_visible=visible,
        survivor=survivor,
        survivor_status=survivor_status,
        after_case=after_case,
        after_records=audit_records(gateway, after_case),
        problems=problems,
        verifies=verifies,
        new_dead_letters=new_dead_letters(dead_before, broker.dead_letter_depths()),
    )


# ------------------------------------------------------ D4: SSE across api restart


@dataclass(frozen=True)
class StreamOutcome:
    """What a case stream saw across an api restart.

    Attributes:
        before: ``seq`` of the frames of the first connection.
        after: ``seq`` of the frames of the resumed connection.
        full: ``seq`` of a fresh replay from 0 (the log as the api serves it).
        first_ended: Why the first connection ended.
        statuses: HTTP status each connection opened with.
        problems: Gaps, repeats and jumps across the two connections.
    """

    before: list[int]
    after: list[int]
    full: list[int]
    first_ended: Optional[str]
    statuses: list[Optional[int]]
    problems: list[str]


def collect_quiet(reader: Any, *, quiet_s: float, timeout_s: float) -> None:
    """Wait until ``reader`` has been silent for ``quiet_s`` (or ended, or
    ``timeout_s`` passed): everything the replay had to say has arrived.
    """
    deadline = time.monotonic() + timeout_s
    last_count, last_change = len(reader.seqs), time.monotonic()
    while time.monotonic() < deadline:
        time.sleep(0.5)
        count = len(reader.seqs)
        if count != last_count:
            last_count, last_change = count, time.monotonic()
        elif time.monotonic() - last_change >= quiet_s or reader.ended is not None:
            return


def stream_across_api_restart(stack: Stack, case_id: str) -> StreamOutcome:
    """Hold an analyst stream open on ``case_id``, restart the api, add an
    event after it is back, reconnect with ``Last-Event-ID`` and compare what
    came with the log.
    """
    docker, gateway = stack.docker, stack.gateway
    gateway.login("po")
    first = gateway.open_stream("analyst", case_id, last_event_id=0)
    resumed = None
    full = None
    try:
        first.wait_for(lambda seqs: len(seqs) >= 1, timeout_s=60.0)
        previous = docker.state("api").started_at
        try:
            docker.restart("api", grace_s=API_GRACE_S)
        finally:
            restore(docker)
        docker.wait_restarted("api", previous_started_at=previous, timeout_s=60)
        first.wait_for(lambda seqs: False, timeout_s=30.0)
        before = first.seqs
        # an event that exists only after the restart
        reply = gateway.post(
            "po",
            f"/cases/{case_id}/discard",
            json={"reason": "resilience drill scratch case, safe to discard"},
        )
        reply.require(200, 204)
        resumed = gateway.open_stream(
            "analyst", case_id, last_event_id=before[-1] if before else 0
        )
        collect_quiet(resumed, quiet_s=6.0, timeout_s=60.0)
        full = gateway.open_stream("analyst", case_id, last_event_id=0)
        collect_quiet(full, quiet_s=6.0, timeout_s=60.0)
        after, everything = resumed.seqs, full.seqs
        head = everything[-1] if everything else 0
        problems = stream_problems([before, after], head=head)
        if sorted({*before, *after}) != everything:
            problems.append("the two connections together differ from the log's replay")
        return StreamOutcome(
            before=before,
            after=after,
            full=everything,
            first_ended=first.ended,
            statuses=[first.status, resumed.status, full.status],
            problems=problems,
        )
    finally:
        for reader in (first, resumed, full):
            if reader is not None:
                reader.stop()


# ------------------------------------------------------- D5: dead letters and replay


@dataclass(frozen=True)
class ReplayOutcome:
    """What parking a finished step and replaying it did.

    Attributes:
        parked: How many more messages the DLQ held after parking.
        shown: The tool's description of the parked message.
        make_status: Exit status of ``make dlq-replay``.
        make_output: What it printed.
        drained: Whether the DLQ went back to where it started.
        skipped_in_log: Whether the harness logged the step as already done.
        records_before: Audit records before the replay.
        records_after: Audit records after it.
        verify: ``audit/verify`` afterwards.
    """

    parked: int
    shown: Optional[dict[str, Any]]
    make_status: int
    make_output: str
    drained: bool
    skipped_in_log: bool
    records_before: int
    records_after: int
    verify: Mapping[str, Any]


def wait_for_depth(
    stack: Stack,
    queue: str,
    target: int,
    *,
    timeout_s: float,
    watch: Optional[str] = None,
) -> tuple[float, int]:
    """Block until ``queue`` holds exactly ``target`` messages; returns
    ``(seconds waited, the most ``watch`` ever held meanwhile)``.

    Raises:
        PollTimeoutError: the depth never reached ``target``.
    """
    began = time.monotonic()
    peak = {"watch": 0}

    def probe() -> Optional[bool]:
        stats = stack.broker.queues()
        if watch is not None and watch in stats:
            peak["watch"] = max(peak["watch"], stats[watch].depth)
        return True if queue in stats and stats[queue].depth == target else None

    poll_until(
        probe,
        what=f"{queue} to hold {target}",
        timeout_s=timeout_s,
        interval_s=1.0,
        tolerate=(CommandError, BrokerAgentError),
    )
    return time.monotonic() - began, peak["watch"]


def replay_a_finished_step(stack: Stack, case_id: str) -> ReplayOutcome:
    """Park a valid ``harness.step`` of an already finished step on the DLQ and
    replay it with ``make dlq-replay``: the consumer must find it done and
    change nothing.
    """
    broker, gateway, docker = stack.broker, stack.gateway, stack.docker
    created = wait_for_record(
        gateway, case_id, "case.created", timeout_s=RECORD_TIMEOUT_S
    )
    run_id, step_id = run_and_step_ids(created)
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    base = baseline_dead_letters(broker).get(STEPS_DLQ, 0)
    records_before = len(audit_records(gateway, case_id))
    event_id = f"evt_drill_replay_{utc_stamp()}"
    since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - 2))
    try:
        broker.publish(
            STEPS_QUEUE,
            step_envelope(case_id, run_id, step_id, "intake.open", event_id=event_id),
            exchange="dlx",
            message_id=event_id,
            message_type="harness.step",
            headers={
                "x-attempt": STEP_ATTEMPTS,
                "x-last-error": "UpstreamUnavailableError",
            },
        )
        wait_for_depth(stack, STEPS_DLQ, base + 1, timeout_s=30.0)
        shown = next(
            (m for m in broker.peek(STEPS_DLQ, limit=50) if names_message(m, event_id)),
            None,
        )
        result = run_make(stack, ["dlq-replay", f"q={STEPS_QUEUE}", f"id={event_id}"])
        wait_for_depth(stack, STEPS_DLQ, base, timeout_s=60.0)
        drained = True
    except PollTimeoutError:
        drained = False
        shown = None
        result = CommandResult(("make",), 1, "", "the drill did not complete")
        broker.purge(STEPS_DLQ, message_id=event_id)
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    logs = docker.logs_since("harness", since)
    return ReplayOutcome(
        parked=1,
        shown=shown,
        make_status=result.returncode,
        make_output=(result.stdout + result.stderr).strip(),
        drained=drained,
        skipped_in_log=step_id in logs and "already done" in logs,
        records_before=records_before,
        records_after=len(audit_records(gateway, case_id)),
        verify=verify_chain(gateway, case_id),
    )


@dataclass(frozen=True)
class PoisonOutcome:
    """What two bad messages on ``caseflow.steps`` did.

    Attributes:
        malformed_dlq_s: Seconds for the unparseable message to reach the DLQ.
        malformed_retry_peak: Most the retry queue held while it did.
        invalid_retry_peak: Most the retry queue held for the invalid payload.
        invalid_dlq_s: Seconds for it to reach the DLQ after its retries.
        shown: The tool's description of each parked message, by id.
        other_case: A case raised while the invalid message was retrying.
        other_work_s: Seconds it took to get its ``workflow.started``.
        purge_refused_status: Exit status of ``make dlq-purge`` without CONFIRM.
        depth_after_refusal: The DLQ's depth after that refusal.
        purge_statuses: Exit statuses of the two confirmed purges.
        drained: Whether the DLQ is back where it started.
        consumers_after: Whether the steps queue still has its consumer.
    """

    malformed_dlq_s: float
    malformed_retry_peak: int
    invalid_retry_peak: int
    invalid_dlq_s: float
    shown: dict[str, dict[str, Any]]
    other_case: str
    other_work_s: float
    purge_refused_status: int
    depth_after_refusal: int
    purge_statuses: list[int]
    drained: bool
    consumers_after: bool


def poison_the_steps_queue(stack: Stack, case_id: str) -> PoisonOutcome:
    """Publish an unparseable message and a well-formed message with an invalid
    payload onto ``caseflow.steps`` and follow them to the DLQ, then purge
    exactly those two messages.
    """
    broker, gateway = stack.broker, stack.gateway
    created = wait_for_record(
        gateway, case_id, "case.created", timeout_s=RECORD_TIMEOUT_S
    )
    run_id, _ = run_and_step_ids(created)
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    base = baseline_dead_letters(broker).get(STEPS_DLQ, 0)
    stamp = utc_stamp()
    malformed_id, invalid_id = f"evt_drill_bad_{stamp}", f"evt_drill_invalid_{stamp}"
    content_type = "application/json" if adapter_fix_deployed(stack) else "text/plain"
    try:
        broker.publish(
            STEPS_QUEUE,
            # cut off mid-object: the envelope's id is readable, the body is not JSON
            f'{{"event_id": "{malformed_id}", "oops": '.encode(),
            message_id=malformed_id,
            message_type="harness.step",
            content_type=content_type,
        )
        malformed_s, malformed_peak = wait_for_depth(
            stack, STEPS_DLQ, base + 1, timeout_s=60.0, watch=STEPS_RETRY
        )
        broker.publish(
            STEPS_QUEUE,
            poison_step_envelope(case_id, run_id, event_id=invalid_id),
            message_id=invalid_id,
            message_type="harness.step",
        )
        began = time.monotonic()
        _, retry_peak = wait_for_depth(
            stack, STEPS_RETRY, 1, timeout_s=60.0, watch=STEPS_RETRY
        )
        other_case = create_scratch_case(gateway, "poison bystander")
        other_began = time.monotonic()
        wait_for_record(
            gateway, other_case, "workflow.started", timeout_s=RECORD_TIMEOUT_S
        )
        other_s = time.monotonic() - other_began
        _, later_peak = wait_for_depth(
            stack,
            STEPS_DLQ,
            base + 2,
            timeout_s=RETRY_CYCLE_TIMEOUT_S,
            watch=STEPS_RETRY,
        )
        invalid_s = time.monotonic() - began
        shown = {
            wanted: m
            for m in broker.peek(STEPS_DLQ, limit=50)
            for wanted in (malformed_id, invalid_id)
            if names_message(m, wanted)
        }
        refused = run_make(
            stack, ["dlq-purge", f"q={STEPS_QUEUE}", f"id={malformed_id}"]
        )
        depth_after_refusal = broker.depth(STEPS_DLQ)
        purges = [
            run_make(
                stack,
                ["dlq-purge", f"q={STEPS_QUEUE}", f"id={message}", "CONFIRM=1"],
            ).returncode
            for message in (malformed_id, invalid_id)
        ]
    finally:
        for message in (malformed_id, invalid_id):
            try:
                broker.purge(STEPS_DLQ, message_id=message)
            except BrokerAgentError:
                logger.warning("could not purge %s", message)
    try:
        wait_for_depth(stack, STEPS_DLQ, base, timeout_s=30.0)
        drained = True
    except PollTimeoutError:
        drained = False
    consumers = broker.queues()[STEPS_QUEUE].consumers >= 1
    return PoisonOutcome(
        malformed_dlq_s=malformed_s,
        malformed_retry_peak=malformed_peak,
        invalid_retry_peak=max(retry_peak, later_peak),
        invalid_dlq_s=invalid_s,
        shown=shown,
        other_case=other_case,
        other_work_s=other_s,
        purge_refused_status=refused.returncode,
        depth_after_refusal=depth_after_refusal,
        purge_statuses=purges,
        drained=drained,
        consumers_after=consumers,
    )


# ------------------------------------------------------------- D6: handoff


@dataclass(frozen=True)
class HandoffOutcome:
    """What a job that cannot run did to the case.

    Attributes:
        job_id: The job published.
        failed_record: H2's ``agent.failed`` audit record for it.
        transition_record: H1's ``agent.failed.after_retry`` record.
        handoff_record: H1's ``human.handoff`` record (phase ``opened``).
        gate_view: What the analyst's ``GET handoff/gates`` shows for the job.
        raw_inputs: What H2 kept of the job's inputs (``agent_raw_inputs``).
        ledger: The case's token ledger afterwards.
        run_state: The run's state in H1's database.
        open_handoff_gates: Open ``handoff`` gates of the run.
        results_for_job: Agent results H1 holds for the job (must be none).
        proposals: ``proposal.*`` records in the chain (must be none).
        problems: Sequence and duplicate problems in the chain.
        verify: ``audit/verify`` afterwards.
        new_dead_letters: Dead-letter queues that grew.
    """

    job_id: str
    failed_record: dict[str, Any]
    transition_record: dict[str, Any]
    handoff_record: dict[str, Any]
    gate_view: Optional[dict[str, Any]]
    raw_inputs: Optional[dict[str, Any]]
    ledger: Optional[dict[str, Any]]
    run_state: Optional[str]
    open_handoff_gates: int
    results_for_job: int
    proposals: list[str]
    problems: list[str]
    verify: Mapping[str, Any]
    new_dead_letters: dict[str, int]


def fail_a_job_into_handoff(stack: Stack, case_id: str) -> HandoffOutcome:
    """Publish an ``agent.run`` that cannot build its context pack and follow
    the ``agent.failed`` it ends in to the run's handoff.
    """
    broker, gateway, mongo = stack.broker, stack.gateway, stack.mongo
    started = wait_for_record(
        gateway, case_id, "workflow.started", timeout_s=RECORD_TIMEOUT_S
    )
    run_id, _ = run_and_step_ids(started)
    pins = started["data"]["pins"]
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    dead_before = baseline_dead_letters(broker)
    job_id = f"job_drill_{utc_stamp()}"
    event_id = f"evt_drill_run_{utc_stamp()}"
    broker.publish(
        "agent.run",
        agent_run_envelope(case_id, run_id, job_id, pins, event_id=event_id),
        exchange="agent.jobs",
        message_id=event_id,
        message_type="agent.run",
        headers={"x-attempt": 0},
    )
    failed = wait_for_record(
        gateway, case_id, "agent.failed", timeout_s=RECORD_TIMEOUT_S
    )
    transition = wait_for_record(
        gateway, case_id, "agent.failed.after_retry", timeout_s=RECORD_TIMEOUT_S
    )
    handoff = wait_for_record(
        gateway, case_id, "human.handoff", timeout_s=RECORD_TIMEOUT_S
    )
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    runs = mongo.find(
        "fcrm_harness", "case_runs", {"_id": run_id}, projection={"state": 1}
    )
    gates = mongo.find(
        "fcrm_harness",
        "gates",
        {"case_id": case_id, "gate_type": "handoff", "status": "open"},
        projection={"_id": 1},
    )
    results = mongo.find(
        "fcrm_harness", "agent_results", {"_id": job_id}, projection={"_id": 1}
    )
    kept = mongo.find("fcrm_harness", "agent_raw_inputs", {"_id": job_id}, limit=1)
    shown = gateway.get("analyst", f"/cases/{case_id}/handoff/gates").require(200)
    views = shown.body.get("gates", []) if isinstance(shown.body, Mapping) else []
    records, problems, verify = settled_chain(stack, case_id, CREATED_ONCE_TYPES)
    return HandoffOutcome(
        job_id=job_id,
        failed_record=failed,
        transition_record=transition,
        handoff_record=handoff,
        gate_view=next((g for g in views if g.get("job_id") == job_id), None),
        raw_inputs=kept[0] if kept else None,
        ledger=case_ledger(stack, case_id),
        run_state=str(runs[0]["state"]) if runs else None,
        open_handoff_gates=len(gates),
        results_for_job=len(results),
        proposals=[
            str(r["type"]) for r in records if str(r["type"]).startswith("proposal.")
        ],
        problems=problems,
        verify=verify,
        new_dead_letters=new_dead_letters(dead_before, broker.dead_letter_depths()),
    )


# ------------------------------------------- D6b: the case token cap, refused


#: The cap a scratch case's ledger is seeded with: far below one intake job's
#: reservation, so Guard 3 refuses the first dispatch.
SEEDED_CAP: Final = 1_000


@dataclass(frozen=True)
class BudgetHandoffOutcome:
    """What a case whose token cap cannot pay for its first job did.

    Attributes:
        seeded: Whether the scratch ledger was created (it had none).
        exhausted_record: The ``budget.exhausted`` audit record.
        handoff_record: H1's ``human.handoff`` record (phase ``opened``).
        gate_view: What the analyst's ``GET handoff/gates`` shows.
        run_state: The run's state in H1's database.
        open_handoff_gates: Open ``handoff`` gates of the run.
        agent_deliveries: Agent jobs the harness received since the upload.
        ledger: The case's ledger afterwards.
        proposals: ``proposal.*`` records in the chain (must be none).
        problems: Sequence and duplicate problems in the chain.
        verify: ``audit/verify`` afterwards.
        new_dead_letters: Dead-letter queues that grew.
    """

    seeded: bool
    exhausted_record: dict[str, Any]
    handoff_record: dict[str, Any]
    gate_view: Optional[dict[str, Any]]
    run_state: Optional[str]
    open_handoff_gates: int
    agent_deliveries: int
    ledger: Optional[dict[str, Any]]
    proposals: list[str]
    problems: list[str]
    verify: Mapping[str, Any]
    new_dead_letters: dict[str, int]


def exhaust_the_case_budget(stack: Stack, case_id: str) -> BudgetHandoffOutcome:
    """Give a scratch case a ledger that cannot pay for one intake job, upload
    its document, and follow the refusal to the run's handoff. No agent job is
    published, so nothing reaches Bedrock.
    """
    docker, broker, gateway, mongo = (
        stack.docker,
        stack.broker,
        stack.gateway,
        stack.mongo,
    )
    started = wait_for_record(
        gateway, case_id, "workflow.started", timeout_s=RECORD_TIMEOUT_S
    )
    run_id, _ = run_and_step_ids(started)
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    dead_before = baseline_dead_letters(broker)
    seeded = mongo.seed_scratch_ledger(case_id, SEEDED_CAP)
    ledger = case_ledger(stack, case_id)
    if not seeded or ledger is None or ledger.get("cap") != SEEDED_CAP:
        # Without the tiny cap in place the upload would end in a real intake
        # call on Bedrock: this drill promises it makes none, so it stops here.
        raise DrillError(
            f"the case ledger of {case_id} does not have the seeded cap of "
            f"{SEEDED_CAP}; not uploading the document (it would call a model)"
        )
    since = rfc3339(datetime.now(UTC) - timedelta(seconds=5))
    upload_document(gateway, case_id)
    exhausted = wait_for_record(
        gateway, case_id, "budget.exhausted", timeout_s=IN_FLIGHT_TIMEOUT_S
    )
    handoff = wait_for_record(
        gateway, case_id, "human.handoff", timeout_s=RECORD_TIMEOUT_S
    )
    wait_for_idle(broker, timeout_s=IDLE_TIMEOUT_S)
    received, _ = deliveries(docker.logs_since("harness", since), IN_FLIGHT_QUEUE)
    runs = mongo.find(
        "fcrm_harness", "case_runs", {"_id": run_id}, projection={"state": 1}
    )
    gates = mongo.find(
        "fcrm_harness",
        "gates",
        {"case_id": case_id, "gate_type": "handoff", "status": "open"},
        projection={"_id": 1},
    )
    shown = gateway.get("analyst", f"/cases/{case_id}/handoff/gates").require(200)
    views = shown.body.get("gates", []) if isinstance(shown.body, Mapping) else []
    records, problems, verify = settled_chain(stack, case_id, CREATED_ONCE_TYPES)
    return BudgetHandoffOutcome(
        seeded=seeded,
        exhausted_record=exhausted,
        handoff_record=handoff,
        gate_view=views[0] if views else None,
        run_state=str(runs[0]["state"]) if runs else None,
        open_handoff_gates=len(gates),
        agent_deliveries=sum(received.values()),
        ledger=case_ledger(stack, case_id),
        proposals=[
            str(r["type"]) for r in records if str(r["type"]).startswith("proposal.")
        ],
        problems=problems,
        verify=verify,
        new_dead_letters=new_dead_letters(dead_before, broker.dead_letter_depths()),
    )


# ------------------------------------------------------------- D7: identity


@dataclass(frozen=True)
class IdentityOutageOutcome:
    """What an already-issued token did while the api restarted.

    Attributes:
        before_status: ``GET auth/me`` with the token before the outage.
        during: Requests with the token while the api was stopped.
        recovery_s: From the api's start until the token answered 200 again.
        after_status: ``GET auth/me`` with the same token afterwards.
        after_user: The user ``auth/me`` named.
        queue_status: ``GET queue`` (a service behind the gateway) afterwards.
        forged_status: ``GET auth/me`` with a tampered token afterwards.
        harness_ready: Whether the harness reported ready afterwards.
    """

    before_status: int
    during: list[Probe]
    recovery_s: float
    after_status: int
    after_user: Optional[str]
    queue_status: int
    forged_status: int
    harness_ready: bool


def forge(token: str) -> str:
    """``token`` with its signature altered (the last character changed)."""
    last = "A" if token[-1] != "A" else "B"
    return token[:-1] + last


def outage_of_identity(stack: Stack, *, hold_s: float = 5.0) -> IdentityOutageOutcome:
    """Stop the api (identity and every service in it), call through the
    gateway with a token issued earlier, start it, and use the same token.
    """
    docker, gateway = stack.docker, stack.gateway
    token = gateway.login("analyst")
    before = gateway.call(None, "GET", "/auth/me", token=token).status
    docker.stop("api", grace_s=API_GRACE_S)
    try:
        during = [
            probe_of(name, gateway.call(None, "GET", path, token=token, timeout_s=45.0))
            for name, path in (("GET /auth/me", "/auth/me"), ("GET /queue", "/queue"))
        ]
        time.sleep(hold_s)
    finally:
        docker.start("api")
    began = time.monotonic()
    try:

        def answers() -> Optional[Reply]:
            reply = gateway.call(None, "GET", "/auth/me", token=token)
            return reply if reply.status == 200 else None

        recovered = poll_until(
            answers,
            what="the earlier token to work again",
            timeout_s=READY_TIMEOUT_S,
            interval_s=2.0,
        )
    finally:
        restore(docker, "api")
    recovery_s = time.monotonic() - began
    user = recovered.body.get("id") if isinstance(recovered.body, Mapping) else None
    queue = gateway.call(None, "GET", "/queue", token=token).status
    forged = gateway.call(None, "GET", "/auth/me", token=forge(token)).status
    return IdentityOutageOutcome(
        before_status=before,
        during=during,
        recovery_s=recovery_s,
        after_status=recovered.status,
        after_user=str(user) if user is not None else None,
        queue_status=queue,
        forged_status=forged,
        harness_ready=docker.readyz("harness").ready,
    )
