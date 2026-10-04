"""S5's persistence layer: packs, votes, tallied outcomes and committee
question/answer threads.

Follows the same shape as ``fcrm_scoring.store`` (S2): a small ``Protocol``
the route layer depends on, an :class:`InMemoryDecisionStore` unit tests
wire directly, and a :class:`MongoDecisionStore` the real app wires at
startup. All the *rules* (hidden votes until quorum, 2-of-3 tally,
never-auto-resolve, release-check gating) live in ``routes.py`` and the
pure modules it composes (``packs.py``, ``outcomes.py``,
``release_checks.py``); this module owns only atomic storage and the one
piece of real concurrency control a vote round needs -- rejecting a second
vote from the same member in the same round.

Internal row shapes (``_PackRow``, ``_VoteRow``, ``_OutcomeRow``,
``_QuestionRow``, ``_AnswerRow``) are **not** part of ``fcrm_contracts``:
they are this service's own storage detail, carrying fields (frozen
``content``, ``open_for_voting``, ``current_round``) the public
``fcrm_contracts.api.decision`` models deliberately do not expose. Routes
only ever return the public contract models built from these rows.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional, Protocol

from fcrm_common.errors import ConflictError, NotFoundError
from fcrm_common.mongo import Repository
from fcrm_contracts.api.decision import (
    CommitteeAnswer,
    CommitteeQuestion,
    OutcomeSet,
    Pack,
    ReleaseChecks,
)
from fcrm_contracts.enums import VoteOption
from pydantic import BaseModel, ConfigDict
from pymongo import IndexModel
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

logger = logging.getLogger(__name__)

QUORUM = 3  # R2: committee quorum is always three.


class _PackRow(BaseModel):
    """The stored pack: the public :class:`Pack` plus the frozen content
    (for ``pack_unchanged`` comparisons) and voting-session bookkeeping.
    """

    model_config = ConfigDict(extra="forbid")

    pack_id: str
    case_id: str
    version: int
    content: dict[str, Any]
    checks: ReleaseChecks
    released_at: Optional[datetime] = None
    superseded_by: Optional[str] = None
    open_for_voting: bool = False
    current_round: int = 1

    def to_pack(self, content_hash: str) -> Pack:
        """Project this row onto the public :class:`Pack` contract."""
        return Pack(
            pack_id=self.pack_id,
            case_id=self.case_id,
            version=self.version,
            content_hash=content_hash,
            checks=self.checks,
            released_at=self.released_at,
            superseded_by=self.superseded_by,
        )


class _VoteRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pack_id: str
    round: int
    member_id: str
    option: VoteOption
    rationale: str
    cast_at: datetime
    voided: bool = False


class _OutcomeRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    outcome: OutcomeSet


class _QuestionRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_id: str
    question: CommitteeQuestion


class _AnswerRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    addendum_id: str
    answer: CommitteeAnswer


class DecisionStore(Protocol):
    """Everything the route layer needs from storage; a case's full pack
    and vote history behind atomic primitives.
    """

    async def latest_pack(self, case_id: str) -> Optional[_PackRow]: ...

    async def get_pack(self, pack_id: str) -> Optional[_PackRow]: ...

    async def insert_pack(self, row: _PackRow) -> None: ...

    async def mark_superseded(self, pack_id: str, new_pack_id: str) -> None: ...

    async def insert_vote(self, row: _VoteRow) -> None: ...

    async def votes_for_round(self, pack_id: str, round_: int) -> list[_VoteRow]: ...

    async def void_votes(self, pack_id: str, round_: int) -> None: ...

    async def set_open_for_voting(self, pack_id: str, open_: bool) -> None: ...

    async def bump_round(self, pack_id: str) -> int: ...

    async def save_outcome(self, outcome: OutcomeSet) -> None: ...

    async def get_outcome(
        self, pack_id: str, round_: Optional[int] = None
    ) -> Optional[OutcomeSet]: ...

    async def insert_question(
        self, question_id: str, question: CommitteeQuestion
    ) -> None: ...

    async def get_question(self, question_id: str) -> Optional[CommitteeQuestion]: ...

    async def insert_answer(
        self, addendum_id: str, answer: CommitteeAnswer
    ) -> None: ...


class InMemoryDecisionStore:
    """Used by unit tests and by anything that does not need Mongo. A
    single ``dict``-backed store; FastAPI/Starlette run one event loop per
    process so no explicit lock is needed for these dict operations to be
    atomic between ``await`` points.
    """

    def __init__(self) -> None:
        self._packs: dict[str, _PackRow] = {}
        self._latest_by_case: dict[str, str] = {}
        self._votes: dict[tuple[str, int], dict[str, _VoteRow]] = {}
        self._outcomes: dict[tuple[str, int], OutcomeSet] = {}
        self._questions: dict[str, CommitteeQuestion] = {}
        self._answers: dict[str, CommitteeAnswer] = {}

    async def latest_pack(self, case_id: str) -> Optional[_PackRow]:
        pack_id = self._latest_by_case.get(case_id)
        return self._packs.get(pack_id) if pack_id else None

    async def get_pack(self, pack_id: str) -> Optional[_PackRow]:
        return self._packs.get(pack_id)

    async def insert_pack(self, row: _PackRow) -> None:
        self._packs[row.pack_id] = row
        self._latest_by_case[row.case_id] = row.pack_id

    async def mark_superseded(self, pack_id: str, new_pack_id: str) -> None:
        row = self._packs.get(pack_id)
        if row is None:
            raise NotFoundError(f"no pack {pack_id}")
        self._packs[pack_id] = row.model_copy(
            update={"superseded_by": new_pack_id, "open_for_voting": False}
        )

    async def insert_vote(self, row: _VoteRow) -> None:
        key = (row.pack_id, row.round)
        round_votes = self._votes.setdefault(key, {})
        if row.member_id in round_votes:
            raise ConflictError(
                "this member already voted in this round",
                rule_id="already_voted",
            )
        round_votes[row.member_id] = row

    async def votes_for_round(self, pack_id: str, round_: int) -> list[_VoteRow]:
        return list(self._votes.get((pack_id, round_), {}).values())

    async def void_votes(self, pack_id: str, round_: int) -> None:
        key = (pack_id, round_)
        round_votes = self._votes.get(key, {})
        self._votes[key] = {
            member_id: row.model_copy(update={"voided": True})
            for member_id, row in round_votes.items()
        }

    async def set_open_for_voting(self, pack_id: str, open_: bool) -> None:
        row = self._packs.get(pack_id)
        if row is None:
            raise NotFoundError(f"no pack {pack_id}")
        self._packs[pack_id] = row.model_copy(update={"open_for_voting": open_})

    async def bump_round(self, pack_id: str) -> int:
        row = self._packs.get(pack_id)
        if row is None:
            raise NotFoundError(f"no pack {pack_id}")
        new_round = row.current_round + 1
        self._packs[pack_id] = row.model_copy(update={"current_round": new_round})
        return new_round

    async def save_outcome(self, outcome: OutcomeSet) -> None:
        self._outcomes[(outcome.pack_id, outcome.round)] = outcome

    async def get_outcome(
        self, pack_id: str, round_: Optional[int] = None
    ) -> Optional[OutcomeSet]:
        if round_ is not None:
            return self._outcomes.get((pack_id, round_))
        rounds = [r for (p, r) in self._outcomes if p == pack_id]
        if not rounds:
            return None
        return self._outcomes[(pack_id, max(rounds))]

    async def insert_question(
        self, question_id: str, question: CommitteeQuestion
    ) -> None:
        self._questions[question_id] = question

    async def get_question(self, question_id: str) -> Optional[CommitteeQuestion]:
        return self._questions.get(question_id)

    async def insert_answer(self, addendum_id: str, answer: CommitteeAnswer) -> None:
        self._answers[addendum_id] = answer


def _packs_repo(db: AsyncDatabase[Any]) -> Repository[_PackRow]:
    """``Repository[_PackRow]`` over ``db.packs``, keyed by ``pack_id``."""
    return Repository(db["packs"], _PackRow, id_of=lambda p: p.pack_id)


def _votes_repo(db: AsyncDatabase[Any]) -> Repository[_VoteRow]:
    """``Repository[_VoteRow]`` over ``db.votes``; ``_id`` is left to Mongo
    so the unique ``{pack_id, round, member_id}`` index is what enforces
    one vote per member per round (D11, ADR-08).
    """
    return Repository(db["votes"], _VoteRow)


def _outcomes_repo(db: AsyncDatabase[Any]) -> Repository[_OutcomeRow]:
    """``Repository[_OutcomeRow]`` over ``db.outcomes``, keyed by
    ``"<pack_id>:<round>"``.
    """
    return Repository(
        db["outcomes"],
        _OutcomeRow,
        id_of=lambda o: f"{o.outcome.pack_id}:{o.outcome.round}",
    )


def _questions_repo(db: AsyncDatabase[Any]) -> Repository[_QuestionRow]:
    """``Repository[_QuestionRow]`` over ``db.questions``, keyed by
    ``question_id``.
    """
    return Repository(db["questions"], _QuestionRow, id_of=lambda q: q.question_id)


def _answers_repo(db: AsyncDatabase[Any]) -> Repository[_AnswerRow]:
    """``Repository[_AnswerRow]`` over ``db.answers``, keyed by
    ``addendum_id``.
    """
    return Repository(db["answers"], _AnswerRow, id_of=lambda a: a.addendum_id)


async def ensure_indexes(db: AsyncDatabase[Any]) -> None:
    """Create every unique index S5's invariants depend on. Called once
    from the lifespan after the client connects.
    """
    await _packs_repo(db).ensure_indexes(
        [IndexModel([("pack_id", 1)], unique=True, name="pack_id_unique")]
    )
    await _votes_repo(db).ensure_indexes(
        [
            IndexModel(
                [("pack_id", 1), ("round", 1), ("member_id", 1)],
                unique=True,
                name="one_vote_per_member_per_round",
            )
        ]
    )
    await _questions_repo(db).ensure_indexes(
        [IndexModel([("question_id", 1)], unique=True, name="question_id_unique")]
    )
    await _answers_repo(db).ensure_indexes(
        [IndexModel([("addendum_id", 1)], unique=True, name="addendum_id_unique")]
    )


class MongoDecisionStore:
    """The real store: ``fcrm_decision.{packs,votes,outcomes,questions,
    answers}``, one MongoDB 8 database per service as everywhere else in
    this repo.
    """

    def __init__(self, db: AsyncDatabase[Any]) -> None:
        self._packs = _packs_repo(db)
        self._votes = _votes_repo(db)
        self._outcomes = _outcomes_repo(db)
        self._questions = _questions_repo(db)
        self._answers = _answers_repo(db)

    async def latest_pack(self, case_id: str) -> Optional[_PackRow]:
        return await self._packs.find_one({"case_id": case_id}, sort=[("version", -1)])

    async def get_pack(self, pack_id: str) -> Optional[_PackRow]:
        return await self._packs.find_one({"pack_id": pack_id})

    async def insert_pack(self, row: _PackRow) -> None:
        await self._packs.insert_one(row)

    async def mark_superseded(self, pack_id: str, new_pack_id: str) -> None:
        await self._packs.update_one(
            {"pack_id": pack_id},
            {"superseded_by": new_pack_id, "open_for_voting": False},
        )

    async def insert_vote(self, row: _VoteRow) -> None:
        try:
            await self._votes.insert_one(row)
        except DuplicateKeyError as exc:
            raise ConflictError(
                "this member already voted in this round",
                rule_id="already_voted",
            ) from exc

    async def votes_for_round(self, pack_id: str, round_: int) -> list[_VoteRow]:
        return await self._votes.find({"pack_id": pack_id, "round": round_})

    async def void_votes(self, pack_id: str, round_: int) -> None:
        await self._votes.collection.update_many(
            {"pack_id": pack_id, "round": round_}, {"$set": {"voided": True}}
        )

    async def set_open_for_voting(self, pack_id: str, open_: bool) -> None:
        await self._packs.update_one({"pack_id": pack_id}, {"open_for_voting": open_})

    async def bump_round(self, pack_id: str) -> int:
        row = await self._packs.find_one_and_update(
            {"pack_id": pack_id}, {"$inc": {"current_round": 1}}
        )
        if row is None:
            raise NotFoundError(f"no pack {pack_id}")
        return row.current_round

    async def save_outcome(self, outcome: OutcomeSet) -> None:
        await self._outcomes.find_one_and_update(
            {"_id": f"{outcome.pack_id}:{outcome.round}"},
            {"outcome": outcome.model_dump(mode="python")},
            upsert=True,
        )

    async def get_outcome(
        self, pack_id: str, round_: Optional[int] = None
    ) -> Optional[OutcomeSet]:
        if round_ is not None:
            row = await self._outcomes.find_one(
                {"outcome.pack_id": pack_id, "outcome.round": round_}
            )
            return row.outcome if row is not None else None
        row = await self._outcomes.find_one(
            {"outcome.pack_id": pack_id}, sort=[("outcome.round", -1)]
        )
        return row.outcome if row is not None else None

    async def insert_question(
        self, question_id: str, question: CommitteeQuestion
    ) -> None:
        await self._questions.insert_one(
            _QuestionRow(question_id=question_id, question=question)
        )

    async def get_question(self, question_id: str) -> Optional[CommitteeQuestion]:
        row = await self._questions.find_one({"question_id": question_id})
        return row.question if row is not None else None

    async def insert_answer(self, addendum_id: str, answer: CommitteeAnswer) -> None:
        await self._answers.insert_one(
            _AnswerRow(addendum_id=addendum_id, answer=answer)
        )
