"""The last mile: what :mod:`fcrm_caseflow.engine` writes to the outbox
(via :class:`fcrm_common.outbox.Outbox`, inside the caller's transaction)
actually reaches the broker once :class:`~fcrm_common.outbox.OutboxRelay`
drains it — proving the dispatch requirement end to end, not just "a row
with the right shape landed in the fake outbox collection" (``test_engine
.py``'s own dispatch assertions stop there; this file goes one hop
further, through a real ``TestRabbitBroker``).
"""

from __future__ import annotations

from typing import Optional

from conftest import FakeCollection, FakeSession
from faststream.rabbit import RabbitBroker, RabbitMessage, TestRabbitBroker
from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.publisher import FastStreamPublisher
from fcrm_caseflow.store import CaseRunStore
from fcrm_caseflow.topology import (
    AGENT_JOBS_EXCHANGE,
    CASE_EVENTS_EXCHANGE,
    passive_exchange,
    passive_queue,
)
from fcrm_common.ids import new_id
from fcrm_common.outbox import OutboxRelay
from fcrm_common.time import utcnow
from fcrm_contracts.enums import AgentName
from fcrm_contracts.events.envelope import Envelope


class _AlwaysHeldLease:
    """A trivial ``fcrm_common.outbox.Lease`` double: this is the only
    relay replica and it never loses the lease mid-test.
    """

    held = True

    async def acquire(self) -> bool:
        return True

    async def release(self) -> None:
        return None


def _env(
    event_type: str, payload: dict[str, object], *, run_id: Optional[str] = None
) -> Envelope:
    return Envelope(
        event_id=new_id("evt"),
        type=event_type,
        case_id="FCRM-2026-0151",
        run_id=run_id,
        occurred_at=utcnow(),
        payload=payload,
    )


async def test_a_dispatched_job_reaches_the_agent_worker_queue_through_the_relay(
    engine: CaseflowEngine,
    session: FakeSession,
    run_store: CaseRunStore,
    outbox_collection: FakeCollection,
) -> None:
    await engine.handle(_env("case.created", {"data": {}}), session)
    run = await run_store.get_by_case_id("FCRM-2026-0151")
    assert run is not None
    run_id = run.snapshot.run_id

    # Drive the real prelude far enough to get one real agent.run dispatch
    # (SCREENING) sitting unpublished in the outbox.
    await engine.handle(
        _env("case.document_stored", {"doc_id": "doc_1"}, run_id=run_id), session
    )
    await engine.handle(_env("agent.done", {"agent": "intake"}, run_id=run_id), session)
    await engine.handle(_env("case.submitted", {}, run_id=run_id), session)

    unpublished = [d for d in outbox_collection.docs if d["published_at"] is None]
    assert any(d["type"] == "agent.run" for d in unpublished)

    broker = RabbitBroker("amqp://guest:guest@localhost:5672/")
    publisher = FastStreamPublisher(broker)
    relay = OutboxRelay(outbox_collection, _AlwaysHeldLease(), publisher)  # type: ignore[arg-type]

    agent_jobs_exchange = passive_exchange(AGENT_JOBS_EXCHANGE)
    received: list[RabbitMessage] = []

    @broker.subscriber(
        passive_queue("agent-worker.run", routing_key="agent.run"),
        agent_jobs_exchange,
    )
    async def _capture(msg: RabbitMessage) -> None:
        received.append(msg)

    # The batch also contains the gate.opened announcement
    # (AGENT_DONE_INTAKE opens the po_confirm_category gate) published onto
    # case.events; give it somewhere to land too, exactly as the real
    # caseflow.events queue's "#" binding would (otherwise TestRabbitBroker
    # raises SubscriberNotFound on that row and the whole batch stops at
    # the first unpublishable row, same as a real broker would behave if
    # a routing key had no queue at all).
    @broker.subscriber(
        passive_queue("caseflow.events", routing_key="#"),
        passive_exchange(CASE_EVENTS_EXCHANGE),
    )
    async def _absorb_case_events(msg: RabbitMessage) -> None:
        return None

    async with TestRabbitBroker(broker):
        published, failed = await relay.publish_batch()

    assert failed is False
    assert published >= 1
    screening_jobs = [
        m
        for m in received
        if Envelope.model_validate_json(m.body).payload.get("agent")
        == AgentName.SCREENING.value
    ]
    assert screening_jobs, "the dispatched screening agent.run never reached the queue"

    # Every row the relay published is now marked so, and will not be
    # republished on the next tick (at-least-once, not infinite).
    assert all(d["published_at"] is not None for d in outbox_collection.docs)
