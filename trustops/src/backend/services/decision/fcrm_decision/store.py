"""What S5's commands and routes need from storage.

Two small protocols, with the rules kept out of them:

* :class:`DecisionStore` is the read side (a bounded query per route, one
  aggregation for the committee queue) and :meth:`DecisionStore.run`, the
  write side's only door.
* :class:`DecisionTx` is the unit of work ``run`` hands to a command: every
  read and write a command makes goes through it, **including the outbox
  enqueue**, so the state change and the event announcing it commit together
  or not at all.

The commands in :mod:`fcrm_decision.commands_pack`, ``commands_vote``,
``commands_thread`` and ``commands_outcome`` hold every rule once and run
unchanged against :class:`fcrm_decision.store_memory.InMemoryDecisionStore`
(unit tests) and :class:`fcrm_decision.store_mongo.MongoDecisionStore` (the
service). A command's callback may run more than once (the Mongo driver
retries a transaction on a transient error), so it does its writes through the
transaction and nothing else.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Optional, Protocol

from fcrm_contracts.events.envelope import Envelope

from fcrm_decision.rows import (
    AnswerRow,
    KeyRow,
    OutcomeRow,
    PackRow,
    QueueEntry,
    ThreadRow,
    VoteRow,
)


class DecisionTx(Protocol):
    """One transaction's worth of reads and writes (see the module docstring)."""

    async def lock_pack(self, pack_id: str) -> Optional[PackRow]:
        """Write-lock the pack and return it as it now is (``None`` if there is
        none). Concurrent transactions that lock the same pack are serialised,
        so a vote's quorum count, a tally and an unlock never interleave.
        """
        ...

    async def get_pack(self, pack_id: str) -> Optional[PackRow]:
        """The pack, without locking it."""
        ...

    async def latest_pack(self, case_id: str) -> Optional[PackRow]:
        """The case's highest-version pack."""
        ...

    async def insert_pack(self, row: PackRow) -> None:
        """Store a new pack.

        Raises:
            fcrm_common.errors.ConflictError: The case already has that version.
        """
        ...

    async def update_pack(self, pack_id: str, changes: Mapping[str, Any]) -> None:
        """Set the named :class:`PackRow` fields."""
        ...

    async def round_votes(self, pack_id: str, round_: int) -> list[VoteRow]:
        """The round's votes that are not voided, oldest first."""
        ...

    async def insert_vote(self, row: VoteRow) -> None:
        """Store a vote.

        Raises:
            fcrm_decision.errors.AlreadyVotedError: The member already voted
                in this round (the unique index is the backstop).
        """
        ...

    async def void_round_votes(self, pack_id: str, round_: int) -> int:
        """Mark the round's votes voided (kept, never deleted); returns how
        many were.
        """
        ...

    async def put_outcome(self, row: OutcomeRow) -> None:
        """Store a round's result or a delegated close."""
        ...

    async def update_outcome(self, row_id: str, changes: Mapping[str, Any]) -> None:
        """Set the named :class:`OutcomeRow` fields."""
        ...

    async def get_outcome(
        self, pack_id: str, round_: Optional[int] = None
    ) -> Optional[OutcomeRow]:
        """A round's result, or the pack's latest round when ``round_`` is None."""
        ...

    async def case_outcome(self, case_id: str) -> Optional[OutcomeRow]:
        """The case's decisive outcome (never a no-majority round), if any."""
        ...

    async def open_committee_pack(self, case_id: str) -> Optional[PackRow]:
        """A released pack of the case that is neither decided nor superseded."""
        ...

    async def insert_thread(self, row: ThreadRow) -> None:
        """Store a question thread."""
        ...

    async def get_thread(self, thread_id: str) -> Optional[ThreadRow]:
        """The thread, answered or not."""
        ...

    async def set_answer(self, thread_id: str, answer: AnswerRow) -> None:
        """Attach the answer to a thread that has none.

        Raises:
            fcrm_decision.errors.AlreadyAnsweredError: It already has one.
        """
        ...

    async def get_key(self, key: str) -> Optional[KeyRow]:
        """The stored ``Idempotency-Key`` row."""
        ...

    async def put_key(self, row: KeyRow) -> None:
        """Store an ``Idempotency-Key`` row."""
        ...

    async def enqueue(self, envelope: Envelope) -> None:
        """Add an event to the outbox in this transaction."""
        ...


class DecisionStore(Protocol):
    """The read side of S5's storage, plus the one door to its writes."""

    async def get_pack(self, pack_id: str) -> Optional[PackRow]:
        """A pack by id."""
        ...

    async def latest_pack(
        self, case_id: str, *, released_only: bool = False
    ) -> Optional[PackRow]:
        """The case's highest-version pack; with ``released_only``, the highest
        that went to committee (a committee member never sees an unreleased one).
        """
        ...

    async def list_packs(
        self, case_id: str, *, released_only: bool, limit: int
    ) -> list[PackRow]:
        """The case's packs, newest version first, at most ``limit``."""
        ...

    async def committee_queue(self, *, limit: int) -> list[QueueEntry]:
        """Released packs that are not superseded, newest release first, each
        joined with who has voted in its current round and (once decided) its
        outcome: one query, no per-pack follow-up.
        """
        ...

    async def round_votes(self, pack_id: str, round_: int) -> list[VoteRow]:
        """The round's votes that are not voided, oldest first."""
        ...

    async def get_outcome(
        self, pack_id: str, round_: Optional[int] = None
    ) -> Optional[OutcomeRow]:
        """A round's result, or the pack's latest round when ``round_`` is None."""
        ...

    async def case_outcome(self, case_id: str) -> Optional[OutcomeRow]:
        """The case's decisive outcome, if any."""
        ...

    async def list_threads(self, pack_id: str, *, limit: int) -> list[ThreadRow]:
        """The pack's threads, oldest first, at most ``limit``."""
        ...

    async def get_thread(self, thread_id: str) -> Optional[ThreadRow]:
        """A thread by id."""
        ...

    async def addenda(self, pack_id: str) -> list[ThreadRow]:
        """The pack's answered threads in addendum order (the addenda)."""
        ...

    async def run[R](self, work: Callable[[DecisionTx], Awaitable[R]]) -> R:
        """Run ``work`` as one transaction and return its result; nothing it
        wrote (including events) is visible unless it returns.
        """
        ...

    async def seed_if_empty(
        self,
        packs: Sequence[PackRow],
        votes: Sequence[VoteRow],
        outcomes: Sequence[OutcomeRow],
    ) -> dict[str, int]:
        """Load seed rows into each collection that is still empty; returns how
        many were loaded per collection.
        """
        ...
