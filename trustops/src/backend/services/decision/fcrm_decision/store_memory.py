"""The in-memory :class:`~fcrm_decision.store.DecisionStore`, for unit tests and
anything that does not need Mongo.

It is as strict as the real one where it matters: a transaction is serialised
by a lock and rolled back if it raises, so a refused vote leaves neither a
vote row nor an outbox row behind; the unique ``(pack, round, member)`` and
``(case, version)`` constraints are enforced; and the committed outbox is a
list a test can read (:attr:`InMemoryDecisionStore.events`).
"""

from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

from fcrm_common.errors import ConflictError
from fcrm_contracts.enums import Outcome
from fcrm_contracts.events.envelope import Envelope
from pydantic import BaseModel

from fcrm_decision.errors import AlreadyAnsweredError, AlreadyVotedError
from fcrm_decision.rows import (
    AnswerRow,
    KeyRow,
    OutcomeRow,
    PackRow,
    QueueEntry,
    ThreadRow,
    VoteRow,
)
from fcrm_decision.store import DecisionTx


@dataclass
class _State:
    packs: dict[str, PackRow] = field(default_factory=dict)
    votes: list[VoteRow] = field(default_factory=list)
    outcomes: dict[str, OutcomeRow] = field(default_factory=dict)
    threads: dict[str, ThreadRow] = field(default_factory=dict)
    keys: dict[str, KeyRow] = field(default_factory=dict)
    outbox: list[Envelope] = field(default_factory=list)


def _set_fields[M: BaseModel](row: M, changes: Mapping[str, Any]) -> M:
    unknown = set(changes) - set(type(row).model_fields)
    if unknown:
        raise ValueError(f"{type(row).__name__} has no field(s) {sorted(unknown)}")
    return row.model_copy(update=dict(changes))


class _MemoryTx:
    """A transaction over the live state; rolled back by the store if it raises."""

    def __init__(self, state: _State) -> None:
        self._s = state

    async def lock_pack(self, pack_id: str) -> Optional[PackRow]:
        pack = self._s.packs.get(pack_id)
        if pack is None:
            return None
        locked = pack.model_copy(update={"ballot_version": pack.ballot_version + 1})
        self._s.packs[pack_id] = locked
        return locked

    async def get_pack(self, pack_id: str) -> Optional[PackRow]:
        return self._s.packs.get(pack_id)

    async def latest_pack(self, case_id: str) -> Optional[PackRow]:
        return _latest(self._s, case_id, released_only=False)

    async def insert_pack(self, row: PackRow) -> None:
        if any(
            p.case_id == row.case_id and p.version == row.version
            for p in self._s.packs.values()
        ):
            raise ConflictError(
                f"case {row.case_id} already has a pack version {row.version}",
                case_id=row.case_id,
            )
        self._s.packs[row.pack_id] = row

    async def update_pack(self, pack_id: str, changes: Mapping[str, Any]) -> None:
        self._s.packs[pack_id] = _set_fields(self._s.packs[pack_id], changes)

    async def round_votes(self, pack_id: str, round_: int) -> list[VoteRow]:
        return _round_votes(self._s, pack_id, round_)

    async def insert_vote(self, row: VoteRow) -> None:
        if any(
            v.pack_id == row.pack_id
            and v.round == row.round
            and v.member_id == row.member_id
            for v in self._s.votes
        ):
            raise AlreadyVotedError("This member already voted in this round.")
        self._s.votes.append(row)

    async def void_round_votes(self, pack_id: str, round_: int) -> int:
        voided = 0
        for index, vote in enumerate(self._s.votes):
            if vote.pack_id == pack_id and vote.round == round_ and not vote.voided:
                self._s.votes[index] = vote.model_copy(update={"voided": True})
                voided += 1
        return voided

    async def put_outcome(self, row: OutcomeRow) -> None:
        self._s.outcomes[row.row_id] = row

    async def update_outcome(self, row_id: str, changes: Mapping[str, Any]) -> None:
        self._s.outcomes[row_id] = _set_fields(self._s.outcomes[row_id], changes)

    async def get_outcome(
        self, pack_id: str, round_: Optional[int] = None
    ) -> Optional[OutcomeRow]:
        return _outcome(self._s, pack_id, round_)

    async def case_outcome(self, case_id: str) -> Optional[OutcomeRow]:
        return _case_outcome(self._s, case_id)

    async def open_committee_pack(self, case_id: str) -> Optional[PackRow]:
        for pack in self._s.packs.values():
            if (
                pack.case_id == case_id
                and pack.released_at is not None
                and not pack.decided
                and not pack.superseded
            ):
                return pack
        return None

    async def insert_thread(self, row: ThreadRow) -> None:
        self._s.threads[row.thread_id] = row

    async def get_thread(self, thread_id: str) -> Optional[ThreadRow]:
        return self._s.threads.get(thread_id)

    async def set_answer(self, thread_id: str, answer: AnswerRow) -> None:
        thread = self._s.threads[thread_id]
        if thread.answer is not None:
            raise AlreadyAnsweredError("This question already has its answer.")
        self._s.threads[thread_id] = thread.model_copy(update={"answer": answer})

    async def get_key(self, key: str) -> Optional[KeyRow]:
        return self._s.keys.get(key)

    async def put_key(self, row: KeyRow) -> None:
        self._s.keys[row.key] = row

    async def enqueue(self, envelope: Envelope) -> None:
        self._s.outbox.append(envelope)


def _latest(state: _State, case_id: str, *, released_only: bool) -> Optional[PackRow]:
    packs = [
        p
        for p in state.packs.values()
        if p.case_id == case_id and (not released_only or p.released_at is not None)
    ]
    return max(packs, key=lambda p: p.version, default=None)


def _round_votes(state: _State, pack_id: str, round_: int) -> list[VoteRow]:
    votes = [
        v
        for v in state.votes
        if v.pack_id == pack_id and v.round == round_ and not v.voided
    ]
    return sorted(votes, key=lambda v: v.cast_at)


def _outcome(
    state: _State, pack_id: str, round_: Optional[int]
) -> Optional[OutcomeRow]:
    rows = [r for r in state.outcomes.values() if r.outcome.pack_id == pack_id]
    if round_ is not None:
        rows = [r for r in rows if r.outcome.round == round_]
    return max(rows, key=lambda r: r.outcome.round, default=None)


def _case_outcome(state: _State, case_id: str) -> Optional[OutcomeRow]:
    rows = [
        r
        for r in state.outcomes.values()
        if r.outcome.case_id == case_id and not r.outcome.no_majority
    ]
    return max(rows, key=lambda r: r.outcome.round, default=None)


class InMemoryDecisionStore:
    """A dict-backed store; see the module docstring."""

    def __init__(self) -> None:
        self._state = _State()
        self._lock = asyncio.Lock()

    @property
    def events(self) -> list[Envelope]:
        """The committed outbox, oldest first."""
        return self._state.outbox

    async def get_pack(self, pack_id: str) -> Optional[PackRow]:
        return self._state.packs.get(pack_id)

    async def latest_pack(
        self, case_id: str, *, released_only: bool = False
    ) -> Optional[PackRow]:
        return _latest(self._state, case_id, released_only=released_only)

    async def list_packs(
        self, case_id: str, *, released_only: bool, limit: int
    ) -> list[PackRow]:
        packs = [
            p
            for p in self._state.packs.values()
            if p.case_id == case_id and (not released_only or p.released_at is not None)
        ]
        return sorted(packs, key=lambda p: p.version, reverse=True)[:limit]

    async def committee_queue(self, *, limit: int) -> list[QueueEntry]:
        state = self._state
        released = [
            p
            for p in state.packs.values()
            if p.released_at is not None and not p.superseded
        ]
        released.sort(
            key=lambda p: (p.released_at is None, p.released_at), reverse=True
        )
        entries: list[QueueEntry] = []
        for pack in released[:limit]:
            voters = [
                v.member_id
                for v in _round_votes(state, pack.pack_id, pack.current_round)
            ]
            row = state.outcomes.get(f"{pack.pack_id}:{pack.current_round}")
            outcome: Optional[Outcome] = (
                row.outcome.outcome
                if row is not None and not row.outcome.no_majority
                else None
            )
            entries.append(QueueEntry.from_pack(pack, voters, outcome))
        return entries

    async def round_votes(self, pack_id: str, round_: int) -> list[VoteRow]:
        return _round_votes(self._state, pack_id, round_)

    async def get_outcome(
        self, pack_id: str, round_: Optional[int] = None
    ) -> Optional[OutcomeRow]:
        return _outcome(self._state, pack_id, round_)

    async def case_outcome(self, case_id: str) -> Optional[OutcomeRow]:
        return _case_outcome(self._state, case_id)

    async def list_threads(self, pack_id: str, *, limit: int) -> list[ThreadRow]:
        threads = [t for t in self._state.threads.values() if t.pack_id == pack_id]
        return sorted(threads, key=lambda t: t.created_at)[:limit]

    async def get_thread(self, thread_id: str) -> Optional[ThreadRow]:
        return self._state.threads.get(thread_id)

    async def addenda(self, pack_id: str) -> list[ThreadRow]:
        answered = [
            t
            for t in self._state.threads.values()
            if t.pack_id == pack_id and t.answer is not None
        ]
        return sorted(answered, key=lambda t: t.answer.addendum_no if t.answer else 0)

    async def run[R](self, work: Callable[[DecisionTx], Awaitable[R]]) -> R:
        """Run ``work`` under the store's lock; if it raises, put the state back
        as it was (a rolled-back transaction), outbox rows included.
        """
        async with self._lock:
            snapshot = copy.deepcopy(self._state)
            try:
                return await work(_MemoryTx(self._state))
            except BaseException:
                self._state.__dict__.update(snapshot.__dict__)
                raise

    async def seed_if_empty(
        self,
        packs: Sequence[PackRow],
        votes: Sequence[VoteRow],
        outcomes: Sequence[OutcomeRow],
    ) -> dict[str, int]:
        loaded = {"packs": 0, "votes": 0, "outcomes": 0}
        state = self._state
        if not state.packs:
            state.packs.update({p.pack_id: p for p in packs})
            loaded["packs"] = len(packs)
        if not state.votes:
            state.votes.extend(votes)
            loaded["votes"] = len(votes)
        if not state.outcomes:
            state.outcomes.update({o.row_id: o for o in outcomes})
            loaded["outcomes"] = len(outcomes)
        return loaded
