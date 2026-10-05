"""The MongoDB-backed :class:`~fcrm_decision.store.DecisionStore`.

One database, ``fcrm_decision``: ``packs``, ``votes``, ``outcomes``,
``questions`` (the committee threads, each carrying its answer), ``outbox``
(:class:`fcrm_common.outbox.Outbox`) and ``idempotency_keys``.

**Concurrency.** A write transaction starts by *writing* the pack document
(``lock_pack`` bumps ``ballot_version``), so two transactions on one pack
conflict, the driver re-runs the loser against the committed state, and a
round never holds a fourth vote, is never tallied twice and is never both
unlocked and decided. The unique ``(pack_id, round, member_id)`` index is the
backstop for "one vote per member per round".

**Outbox.** :meth:`_MongoTx.enqueue` writes the event through the same
session, so a vote and its ``vote.cast`` row commit together, and a refused
vote (a duplicate, a full round) leaves neither.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Optional

from fcrm_common.errors import ConflictError
from fcrm_common.mongo import Repository, with_transaction
from fcrm_common.outbox import Outbox
from fcrm_common.seedload import load_if_empty
from fcrm_contracts.events.envelope import Envelope
from pydantic import BaseModel
from pymongo import ASCENDING, DESCENDING, IndexModel
from pymongo.asynchronous.client_session import AsyncClientSession
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

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

logger = logging.getLogger(__name__)

#: How long an ``Idempotency-Key`` replays (design A: 24 hours).
KEY_TTL_S = 86_400

#: Indexes an earlier build created that this one replaces; dropped if present
#: (a unique ``(case_id, version)`` cannot share a name with the old
#: non-unique one, and ``question_id_unique`` would reject every thread, which
#: has no ``question_id``).
_LEGACY_INDEXES: tuple[tuple[str, str], ...] = (
    ("packs", "case_id_version"),
    ("questions", "question_id_unique"),
)


def _check_fields(model: type[Any], changes: Mapping[str, Any]) -> None:
    unknown = set(changes) - set(model.model_fields)
    if unknown:
        raise ValueError(f"{model.__name__} has no field(s) {sorted(unknown)}")


class _Repos:
    """The repositories over one database, built once per store."""

    def __init__(self, db: AsyncDatabase[Any]) -> None:
        self.packs = Repository(db["packs"], PackRow, id_of=lambda p: p.pack_id)
        # ``_id`` is left to Mongo: the unique (pack, round, member) index is
        # what enforces one vote per member per round.
        self.votes = Repository(db["votes"], VoteRow)
        self.outcomes = Repository(db["outcomes"], OutcomeRow, id_of=lambda o: o.row_id)
        self.threads = Repository(
            db["questions"], ThreadRow, id_of=lambda t: t.thread_id
        )
        self.keys = Repository(db["idempotency_keys"], KeyRow, id_of=lambda k: k.key)


async def _drop_if_present(collection: AsyncCollection[Any], name: str) -> None:
    if name in await collection.index_information():
        await collection.drop_index(name)
        logger.info(
            "dropped a legacy index",
            extra={"collection": collection.full_name, "index": name},
        )


async def ensure_indexes(db: AsyncDatabase[Any], outbox: Outbox) -> None:
    """Create every index S5's queries and invariants depend on (idempotent).

    One per query path: ``latest_pack``/``list_packs`` (``case_id, version``,
    unique so two builds cannot mint the same version), the committee queue
    (``released_at``), a round's votes (the unique ``pack_id, round,
    member_id`` also serves ``pack_id, round``), outcomes by pack and by case,
    a pack's threads, and the idempotency-key TTL. The outbox brings its own.
    """
    for collection_name, index_name in _LEGACY_INDEXES:
        await _drop_if_present(db[collection_name], index_name)
    repos = _Repos(db)
    await repos.packs.ensure_indexes(
        [
            IndexModel([("pack_id", ASCENDING)], unique=True, name="pack_id_unique"),
            IndexModel(
                [("case_id", ASCENDING), ("version", ASCENDING)],
                unique=True,
                name="case_id_version_unique",
            ),
            IndexModel([("released_at", DESCENDING)], name="released_queue"),
        ]
    )
    await repos.votes.ensure_indexes(
        [
            IndexModel(
                [
                    ("pack_id", ASCENDING),
                    ("round", ASCENDING),
                    ("member_id", ASCENDING),
                ],
                unique=True,
                name="one_vote_per_member_per_round",
            )
        ]
    )
    await repos.outcomes.ensure_indexes(
        [
            IndexModel(
                [("outcome.pack_id", ASCENDING), ("outcome.round", DESCENDING)],
                name="outcome_pack_round",
            ),
            IndexModel(
                [("outcome.case_id", ASCENDING), ("outcome.round", DESCENDING)],
                name="outcome_case_round",
            ),
        ]
    )
    await repos.threads.ensure_indexes(
        [
            IndexModel(
                [("pack_id", ASCENDING), ("created_at", ASCENDING)],
                name="pack_created",
            )
        ]
    )
    await repos.keys.ensure_indexes(
        [IndexModel([("at", ASCENDING)], expireAfterSeconds=KEY_TTL_S, name="key_ttl")]
    )
    await outbox.ensure_indexes()


class _MongoTx:
    """One transaction's reads and writes, all through ``session``."""

    def __init__(
        self, session: AsyncClientSession, repos: _Repos, outbox: Outbox
    ) -> None:
        self._session = session
        self._r = repos
        self._outbox = outbox

    async def lock_pack(self, pack_id: str) -> Optional[PackRow]:
        return await self._r.packs.find_one_and_update(
            {"pack_id": pack_id},
            {"$inc": {"ballot_version": 1}},
            session=self._session,
        )

    async def get_pack(self, pack_id: str) -> Optional[PackRow]:
        return await self._r.packs.find_one({"pack_id": pack_id}, session=self._session)

    async def latest_pack(self, case_id: str) -> Optional[PackRow]:
        return await self._r.packs.find_one(
            {"case_id": case_id}, sort=[("version", -1)], session=self._session
        )

    async def insert_pack(self, row: PackRow) -> None:
        try:
            await self._r.packs.insert_one(row, session=self._session)
        except DuplicateKeyError as exc:
            raise ConflictError(
                f"case {row.case_id} already has a pack version {row.version}",
                case_id=row.case_id,
            ) from exc

    async def update_pack(self, pack_id: str, changes: Mapping[str, Any]) -> None:
        _check_fields(PackRow, changes)
        await self._r.packs.update_one(
            {"pack_id": pack_id}, changes, session=self._session
        )

    async def round_votes(self, pack_id: str, round_: int) -> list[VoteRow]:
        return await self._r.votes.find(
            {"pack_id": pack_id, "round": round_, "voided": {"$ne": True}},
            sort=[("cast_at", 1)],
            session=self._session,
        )

    async def insert_vote(self, row: VoteRow) -> None:
        try:
            await self._r.votes.insert_one(row, session=self._session)
        except DuplicateKeyError as exc:
            raise AlreadyVotedError("This member already voted in this round.") from exc

    async def void_round_votes(self, pack_id: str, round_: int) -> int:
        result = await self._r.votes.collection.update_many(
            {"pack_id": pack_id, "round": round_, "voided": {"$ne": True}},
            {"$set": {"voided": True}},
            session=self._session,
        )
        return int(result.modified_count)

    async def put_outcome(self, row: OutcomeRow) -> None:
        await self._r.outcomes.insert_one(row, session=self._session)

    async def update_outcome(self, row_id: str, changes: Mapping[str, Any]) -> None:
        _check_fields(OutcomeRow, changes)
        await self._r.outcomes.update_one(
            {"_id": row_id}, _dump(changes), session=self._session
        )

    async def get_outcome(
        self, pack_id: str, round_: Optional[int] = None
    ) -> Optional[OutcomeRow]:
        return await _find_outcome(self._r, pack_id, round_, self._session)

    async def case_outcome(self, case_id: str) -> Optional[OutcomeRow]:
        return await _find_case_outcome(self._r, case_id, self._session)

    async def open_committee_pack(self, case_id: str) -> Optional[PackRow]:
        return await self._r.packs.find_one(
            {
                "case_id": case_id,
                "released_at": {"$ne": None},
                "decided": {"$ne": True},
                "superseded_at": None,
                "superseded_by": None,
            },
            session=self._session,
        )

    async def insert_thread(self, row: ThreadRow) -> None:
        await self._r.threads.insert_one(row, session=self._session)

    async def get_thread(self, thread_id: str) -> Optional[ThreadRow]:
        return await self._r.threads.find_one(
            {"thread_id": thread_id}, session=self._session
        )

    async def set_answer(self, thread_id: str, answer: AnswerRow) -> None:
        result = await self._r.threads.collection.update_one(
            {"thread_id": thread_id, "answer": None},
            {"$set": {"answer": _dump({"answer": answer})["answer"]}},
            session=self._session,
        )
        if result.matched_count == 0:
            raise AlreadyAnsweredError("This question already has its answer.")

    async def get_key(self, key: str) -> Optional[KeyRow]:
        return await self._r.keys.find_one({"_id": key}, session=self._session)

    async def put_key(self, row: KeyRow) -> None:
        await self._r.keys.insert_one(row, session=self._session)

    async def enqueue(self, envelope: Envelope) -> None:
        await self._outbox.enqueue(self._session, envelope)


def _dump(changes: Mapping[str, Any]) -> dict[str, Any]:
    """``changes`` with any pydantic model inside dumped to plain data."""
    return {
        key: value.model_dump(mode="python") if isinstance(value, BaseModel) else value
        for key, value in changes.items()
    }


async def _find_outcome(
    repos: _Repos,
    pack_id: str,
    round_: Optional[int],
    session: Optional[AsyncClientSession],
) -> Optional[OutcomeRow]:
    query: dict[str, Any] = {"outcome.pack_id": pack_id}
    if round_ is not None:
        query["outcome.round"] = round_
    return await repos.outcomes.find_one(
        query, sort=[("outcome.round", -1)], session=session
    )


async def _find_case_outcome(
    repos: _Repos, case_id: str, session: Optional[AsyncClientSession]
) -> Optional[OutcomeRow]:
    return await repos.outcomes.find_one(
        {"outcome.case_id": case_id, "outcome.no_majority": {"$ne": True}},
        sort=[("outcome.round", -1)],
        session=session,
    )


class MongoDecisionStore:
    """The real store; see the module docstring.

    Args:
        db: The ``fcrm_decision`` database.
        outbox: The outbox over ``db["outbox"]`` (same database as the state it
            announces, so one transaction covers both).
    """

    def __init__(self, db: AsyncDatabase[Any], outbox: Outbox) -> None:
        self._client = db.client
        self._r = _Repos(db)
        self._outbox = outbox

    async def get_pack(self, pack_id: str) -> Optional[PackRow]:
        return await self._r.packs.find_one({"pack_id": pack_id})

    async def latest_pack(
        self, case_id: str, *, released_only: bool = False
    ) -> Optional[PackRow]:
        query: dict[str, Any] = {"case_id": case_id}
        if released_only:
            query["released_at"] = {"$ne": None}
        return await self._r.packs.find_one(query, sort=[("version", -1)])

    async def list_packs(
        self, case_id: str, *, released_only: bool, limit: int
    ) -> list[PackRow]:
        query: dict[str, Any] = {"case_id": case_id}
        if released_only:
            query["released_at"] = {"$ne": None}
        return await self._r.packs.find(query, sort=[("version", -1)], limit=limit)

    async def committee_queue(self, *, limit: int) -> list[QueueEntry]:
        pipeline: list[dict[str, Any]] = [
            {
                "$match": {
                    "released_at": {"$ne": None},
                    "superseded_at": None,
                    "superseded_by": None,
                }
            },
            {"$sort": {"released_at": -1, "pack_id": 1}},
            {"$limit": limit},
            {
                "$lookup": {
                    "from": "votes",
                    "let": {"pid": "$pack_id", "rnd": "$current_round"},
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {
                                    "$and": [
                                        {"$eq": ["$pack_id", "$$pid"]},
                                        {"$eq": ["$round", "$$rnd"]},
                                    ]
                                },
                                "voided": {"$ne": True},
                            }
                        },
                        {"$project": {"_id": 0, "member_id": 1}},
                    ],
                    "as": "ballot",
                }
            },
            {
                "$lookup": {
                    "from": "outcomes",
                    "let": {
                        "oid": {
                            "$concat": [
                                "$pack_id",
                                ":",
                                {"$toString": "$current_round"},
                            ]
                        }
                    },
                    "pipeline": [
                        {
                            "$match": {
                                "$expr": {"$eq": ["$_id", "$$oid"]},
                                "outcome.no_majority": {"$ne": True},
                            }
                        },
                        {"$project": {"_id": 0, "outcome": "$outcome.outcome"}},
                    ],
                    "as": "decided_outcome",
                }
            },
            {
                "$project": {
                    "_id": 0,
                    "pack_id": 1,
                    "case_id": 1,
                    "version": 1,
                    "released_at": 1,
                    "decided": 1,
                    "title": "$content.title",
                    "analyst_name": "$content.analyst_name",
                    "change_types": "$content.change_types",
                    "band": "$content.score.band",
                    "band_label": "$content.score.band_label",
                    "voters": "$ballot.member_id",
                    "outcome": {"$arrayElemAt": ["$decided_outcome.outcome", 0]},
                }
            },
        ]
        rows = await self._r.packs.aggregate(pipeline)
        return [QueueEntry.model_validate(row) for row in rows]

    async def round_votes(self, pack_id: str, round_: int) -> list[VoteRow]:
        return await self._r.votes.find(
            {"pack_id": pack_id, "round": round_, "voided": {"$ne": True}},
            sort=[("cast_at", 1)],
        )

    async def get_outcome(
        self, pack_id: str, round_: Optional[int] = None
    ) -> Optional[OutcomeRow]:
        return await _find_outcome(self._r, pack_id, round_, None)

    async def case_outcome(self, case_id: str) -> Optional[OutcomeRow]:
        return await _find_case_outcome(self._r, case_id, None)

    async def list_threads(self, pack_id: str, *, limit: int) -> list[ThreadRow]:
        return await self._r.threads.find(
            {"pack_id": pack_id}, sort=[("created_at", 1)], limit=limit
        )

    async def get_thread(self, thread_id: str) -> Optional[ThreadRow]:
        return await self._r.threads.find_one({"thread_id": thread_id})

    async def addenda(self, pack_id: str) -> list[ThreadRow]:
        return await self._r.threads.find(
            {"pack_id": pack_id, "answer": {"$ne": None}},
            sort=[("answer.addendum_no", 1)],
        )

    async def run[R](self, work: Callable[[DecisionTx], Awaitable[R]]) -> R:
        """Run ``work`` in one Mongo transaction (the driver re-runs it on a
        transient error), handing it the transaction's reads and writes.
        """

        async def _in_session(session: AsyncClientSession) -> R:
            return await work(_MongoTx(session, self._r, self._outbox))

        return await with_transaction(self._client, _in_session)

    async def seed_if_empty(
        self,
        packs: Sequence[PackRow],
        votes: Sequence[VoteRow],
        outcomes: Sequence[OutcomeRow],
    ) -> dict[str, int]:
        """Bulk-load seed rows, one ``insert_many`` per collection, into each
        collection that is still empty (so a restart never duplicates them).
        """
        r = self._r
        return {
            "packs": await load_if_empty(
                r.packs, [r.packs.to_document(p) for p in packs]
            ),
            "votes": await load_if_empty(
                r.votes, [r.votes.to_document(v) for v in votes]
            ),
            "outcomes": await load_if_empty(
                r.outcomes, [r.outcomes.to_document(o) for o in outcomes]
            ),
        }
