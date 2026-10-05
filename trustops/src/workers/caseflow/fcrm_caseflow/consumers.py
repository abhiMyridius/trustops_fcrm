"""Caseflow's two bus consumers (design A §4.1's real, deployed topology —
see :mod:`fcrm_caseflow.topology`'s module docstring for exactly which
queues this is and is not): ``caseflow.events`` (every case-lifecycle,
gate, pack, vote, committee, outcome and index event) and
``caseflow.agent-results`` (``agent.done``/``agent.failed``). Both delegate
to one :class:`~fcrm_caseflow.engine.CaseflowEngine` — the queue only
decides which wire events arrive, not how a resolved trigger is applied.
"""

from __future__ import annotations

from typing import Optional

from fcrm_common.events import Consumer, Publisher
from fcrm_common.mongo import Document
from fcrm_contracts.events.envelope import Envelope
from pymongo import AsyncMongoClient
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection

from fcrm_caseflow.engine import CaseflowEngine
from fcrm_caseflow.topology import CASEFLOW_AGENT_RESULTS_QUEUE, CASEFLOW_EVENTS_QUEUE


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
