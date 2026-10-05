"""Caseflow's three bus consumers (design A §4.1's real, deployed topology —
see :mod:`fcrm_caseflow.topology`'s module docstring for exactly which
queues this is and is not): ``caseflow.events`` (every case-lifecycle,
gate, pack, vote, committee, outcome and index event),
``caseflow.agent-results`` (``agent.done``/``agent.failed``) and
``caseflow.steps`` (``harness.step``, ADR-28). The first two delegate to one
:class:`~fcrm_caseflow.engine.CaseflowEngine` — the queue only decides which
wire events arrive, not how a resolved trigger is applied. The third hands a
step to :class:`~fcrm_caseflow.steps.StepRunner`.
"""

from __future__ import annotations

from typing import Optional

from fcrm_common.events import Consumer, Publisher
from fcrm_common.mongo import Document
from fcrm_contracts.events.envelope import Envelope
from fcrm_contracts.events.payloads import HarnessStep
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.steps import StepRunner
from fcrm_caseflow.topology import (
    CASEFLOW_AGENT_RESULTS_QUEUE,
    CASEFLOW_EVENTS_QUEUE,
    CASEFLOW_STEPS_QUEUE,
    HARNESS_STEP_EVENT,
)


class CaseEventsConsumer(Consumer):
    """Consumes :data:`~fcrm_caseflow.topology.CASEFLOW_EVENTS_QUEUE`."""

    def __init__(
        self,
        consumed: AsyncCollection[Document],
        publisher: Publisher,
        *,
        engine: CaseflowEngine,
        client: Optional[AsyncMongoClient[Document]] = None,
        max_attempts: int = 3,
    ) -> None:
        super().__init__(
            CASEFLOW_EVENTS_QUEUE,
            consumed,
            publisher,
            client=client,
            max_attempts=max_attempts,
        )
        self._engine = engine

    async def handle(
        self, envelope: Envelope, session: Optional[AsyncClientSession]
    ) -> None:
        """Apply one case event; ``harness.step`` is ignored.

        The ``#`` binding delivers H1's own ``harness.step`` messages here as
        well as to ``caseflow.steps``; they are work for the step consumer,
        not a trigger, so this one returns before the engine sees them.
        """
        if envelope.type == HARNESS_STEP_EVENT:
            return
        await self._engine.handle(envelope, session)


class AgentResultsConsumer(Consumer):
    """Consumes :data:`~fcrm_caseflow.topology.CASEFLOW_AGENT_RESULTS_QUEUE`."""

    def __init__(
        self,
        consumed: AsyncCollection[Document],
        publisher: Publisher,
        *,
        engine: CaseflowEngine,
        client: Optional[AsyncMongoClient[Document]] = None,
        max_attempts: int = 3,
    ) -> None:
        super().__init__(
            CASEFLOW_AGENT_RESULTS_QUEUE,
            consumed,
            publisher,
            client=client,
            max_attempts=max_attempts,
        )
        self._engine = engine

    async def handle(
        self, envelope: Envelope, session: Optional[AsyncClientSession]
    ) -> None:
        await self._engine.handle(envelope, session)


class HarnessStepConsumer(Consumer):
    """Consumes :data:`~fcrm_caseflow.topology.CASEFLOW_STEPS_QUEUE`.

    Built with ``client=None``: dedupe is the non-transactional path (the
    marker is written after the handler succeeds), because a step's work is
    HTTP calls that must not sit inside a Mongo transaction. That is safe
    because :class:`~fcrm_caseflow.steps.StepRunner` is idempotent by
    ``step_id`` (a step with a ``step_done`` row is a no-op), so a crash
    between finishing and marking only costs one cheap re-check. A failing
    step raises out of :meth:`handle` and the base class retries it, then
    dead-letters it: it is never acknowledged as done.
    """

    def __init__(
        self,
        consumed: AsyncCollection[Document],
        publisher: Publisher,
        *,
        runner: StepRunner,
        max_attempts: int = 3,
    ) -> None:
        super().__init__(
            CASEFLOW_STEPS_QUEUE,
            consumed,
            publisher,
            client=None,
            max_attempts=max_attempts,
        )
        self._runner = runner

    async def handle(
        self, envelope: Envelope, session: Optional[AsyncClientSession]
    ) -> None:
        """Validate the payload as a :class:`HarnessStep` and run it.

        Raises:
            pydantic.ValidationError: the payload is not a ``HarnessStep``
                (an unknown ``step`` kind included); retried, then parked.
            fcrm_common.errors.FcrmError: the step failed; see
                :meth:`fcrm_caseflow.steps.StepRunner.run`.
        """
        await self._runner.run(HarnessStep.model_validate(envelope.payload))
