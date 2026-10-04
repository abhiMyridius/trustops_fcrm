"""S5's persistence layer: packs, votes, tallied outcomes and committee
question/answer threads.

Follows the same shape as ``fcrm_scoring.store`` (S2): a small ``Protocol``
the route layer depends on, an :class:`InMemoryDecisionStore` unit tests
wire directly, and a :class:`MongoDecisionStore` the real app wires at
startup. All the *rules* (hidden votes until quorum, 2-of-3 tally,
never-auto-resolve, release-check gating) live in ``routes.py`` and the
pure modules it composes (``packs.py``, ``outcomes.py``,
``release_checks.py``); this module owns atomic storage and the two pieces
of real concurrency control a vote round needs:

- :meth:`DecisionStore.record_vote` -- recording a vote, the quorum check
  and (on the vote that completes quorum) the tally and the round's close
  are **one** atomic step. Exactly one caller can take the last seat, so a
  round never holds more than :data:`QUORUM` votes and is tallied once
  (the old insert-then-count sequence let two concurrent members both
  insert, both tally and bump the round twice). In Mongo it is one
  transaction that also writes the pack document, so concurrent votes on
  one pack hit a write conflict and are serialised by the driver's retry;
  in memory it is a section with no ``await`` in it, which is atomic on
  one event loop.
- :meth:`DecisionStore.unlock_for_changed_numbers` -- the R6 unlock is
  refused once the pack has a decisive outcome, in the same atomic step as
  closing the pack and voiding the open round's votes, so an unlock and the
  vote that decides the round cannot both win.

Internal row shapes (``_PackRow``, ``_VoteRow``, ``_OutcomeRow``,
``_QuestionRow``, ``_AnswerRow``) are **not** part of ``fcrm_contracts``:
they are this service's own storage detail, carrying fields (frozen
``content``, ``open_for_voting``, ``current_round``) the public
``fcrm_contracts.api.decision`` models deliberately do not expose. Routes
only ever return the public contract models built from these rows.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Optional, Protocol

from fcrm_common.errors import ConflictError, NotFoundError, RuleViolationError
from fcrm_common.mongo import Repository, with_transaction
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
from pymongo.asynchronous.client_session import AsyncClientSession
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
    #: A round of this pack reached a decisive (not ``no_majority``)
    #: outcome. Set in the same atomic step as that outcome; once true the
    #: pack never reopens and an R6 unlock is refused.
    decided: bool = False
    #: Bumped by every recorded vote. Its only job is to make each vote's
    #: Mongo transaction *write* the pack document, so two concurrent votes
    #: on one pack conflict and are serialised (see ``record_vote``).
    ballot_version: int = 0

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


class VoteRecorded(BaseModel):
    """What :meth:`DecisionStore.record_vote` reports: how many valid votes
    the round now holds and, only for the vote that completed quorum, the
    outcome that was tallied and stored in the same atomic step.
    """

    model_config = ConfigDict(extra="forbid")

    votes_cast: int
    outcome: Optional[OutcomeSet] = None


#: Pure function the store calls, inside its atomic step, on the full
#: ballot of a round that has just reached quorum (``routes._decide``).
Decide = Callable[[list[_VoteRow]], OutcomeSet]


def _not_open_error(pack: Optional[_PackRow], row: _VoteRow) -> Exception:
    """Why a vote could not be recorded, re-derived from the pack as it is
    now (the route's pre-checks passed, so the pack changed under it).
    """
    if pack is None:
        return NotFoundError(f"no pack {row.pack_id}")
    if pack.open_for_voting and row.round != pack.current_round:
        return ConflictError(
            f"stale round: this pack's current round is {pack.current_round}",
            rule_id="stale_round",
        )
    return RuleViolationError(
        "pack is not open for voting (release checks incomplete, superseded, "
        "decided, or awaiting corrected numbers)",
        rule_id="pack_not_open",
    )


def _round_full_error() -> ConflictError:
    return ConflictError(
        f"this round already has its {QUORUM} votes", rule_id="round_full"
    )


def _already_voted_error() -> ConflictError:
    return ConflictError(
        "this member already voted in this round", rule_id="already_voted"
    )


class DecisionStore(Protocol):
    """Everything the route layer needs from storage; a case's full pack
    and vote history behind atomic primitives.
    """

    async def latest_pack(self, case_id: str) -> Optional[_PackRow]: ...

    async def get_pack(self, pack_id: str) -> Optional[_PackRow]: ...

    async def insert_pack(self, row: _PackRow) -> None: ...

    async def mark_superseded(self, pack_id: str, new_pack_id: str) -> None: ...

    async def record_vote(self, row: _VoteRow, decide: Decide) -> VoteRecorded:
        """Atomically: refuse unless the pack is open, undecided and on
        ``row.round``; refuse a second vote by the member and a vote into a
        full round; store the vote; and if it is the :data:`QUORUM`-th,
        call ``decide`` on the round's ballot, store that outcome and either
        open the next round (``no_majority``) or close the pack (decided).

        Raises:
            NotFoundError: No such pack.
            RuleViolationError: ``pack_not_open``.
            ConflictError: ``stale_round``, ``already_voted``,
                ``round_full``.
        """
        ...

    async def unlock_for_changed_numbers(self, pack_id: str) -> bool:
        """Atomically close ``pack_id`` to votes and void its open round's
        votes (R6), unless it already has a decisive outcome.

        Returns:
            ``False`` (and nothing changes) when the pack is decided.
        """
        ...

    async def votes_for_round(self, pack_id: str, round_: int) -> list[_VoteRow]: ...

    async def void_votes(self, pack_id: str, round_: int) -> None: ...

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

    async def record_vote(self, row: _VoteRow, decide: Decide) -> VoteRecorded:
        # Atomic by construction: nothing between here and the return
        # awaits, so no other request can run in the middle of it.
        pack = self._packs.get(row.pack_id)
        if (
            pack is None
            or not pack.open_for_voting
            or pack.decided
            or pack.current_round != row.round
        ):
            raise _not_open_error(pack, row)
        round_votes = self._votes.setdefault((row.pack_id, row.round), {})
        if row.member_id in round_votes:
            raise _already_voted_error()
        valid = [v for v in round_votes.values() if not v.voided]
        if len(valid) >= QUORUM:
            raise _round_full_error()
        round_votes[row.member_id] = row
        valid.append(row)
        pack = pack.model_copy(update={"ballot_version": pack.ballot_version + 1})
        outcome: Optional[OutcomeSet] = None
        if len(valid) == QUORUM:
            outcome = decide(valid)
            self._outcomes[(outcome.pack_id, outcome.round)] = outcome
            if outcome.no_majority:
                pack = pack.model_copy(update={"current_round": row.round + 1})
            else:
                pack = pack.model_copy(
                    update={"open_for_voting": False, "decided": True}
                )
        self._packs[row.pack_id] = pack
        return VoteRecorded(votes_cast=len(valid), outcome=outcome)

    async def unlock_for_changed_numbers(self, pack_id: str) -> bool:
        pack = self._packs.get(pack_id)
        if pack is None:
            raise NotFoundError(f"no pack {pack_id}")
        if pack.decided or self._has_decisive_outcome(pack_id):
            return False
        self._packs[pack_id] = pack.model_copy(update={"open_for_voting": False})
        self._void(pack_id, pack.current_round)
        return True

    def _has_decisive_outcome(self, pack_id: str) -> bool:
        return any(
            p == pack_id and not o.no_majority for (p, _), o in self._outcomes.items()
        )

    def _void(self, pack_id: str, round_: int) -> None:
        key = (pack_id, round_)
        round_votes = self._votes.get(key, {})
        self._votes[key] = {
            member_id: row.model_copy(update={"voided": True})
            for member_id, row in round_votes.items()
        }

    async def votes_for_round(self, pack_id: str, round_: int) -> list[_VoteRow]:
        return list(self._votes.get((pack_id, round_), {}).values())

    async def void_votes(self, pack_id: str, round_: int) -> None:
        self._void(pack_id, round_)

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
        self._client = db.client
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

    async def record_vote(self, row: _VoteRow, decide: Decide) -> VoteRecorded:
        """One transaction. Its first statement *writes* the pack document
        (``$inc ballot_version``) under the open/undecided/current-round
        guard, so a concurrent vote on the same pack hits a write conflict
        (``TransientTransactionError``) and ``with_transaction`` re-runs it
        against the committed state: votes on one pack are serialised, and
        the quorum count below is exact.
        """

        async def _do(session: AsyncClientSession) -> VoteRecorded:
            pack = await self._packs.find_one_and_update(
                {
                    "pack_id": row.pack_id,
                    "open_for_voting": True,
                    "decided": {"$ne": True},
                    "current_round": row.round,
                },
                {"$inc": {"ballot_version": 1}},
                session=session,
            )
            if pack is None:
                current = await self._packs.find_one(
                    {"pack_id": row.pack_id}, session=session
                )
                raise _not_open_error(current, row)
            ballot_filter = {
                "pack_id": row.pack_id,
                "round": row.round,
                "voided": {"$ne": True},
            }
            if await self._votes.count(ballot_filter, session=session) >= QUORUM:
                raise _round_full_error()
            try:
                await self._votes.insert_one(row, session=session)
            except DuplicateKeyError as exc:
                raise _already_voted_error() from exc
            ballot = await self._votes.find(ballot_filter, session=session)
            if len(ballot) < QUORUM:
                return VoteRecorded(votes_cast=len(ballot))
            outcome = decide(ballot)
            await self._outcomes.find_one_and_update(
                {"_id": f"{outcome.pack_id}:{outcome.round}"},
                {"outcome": outcome.model_dump(mode="python")},
                upsert=True,
                session=session,
            )
            close: dict[str, Any] = (
                {"$inc": {"current_round": 1}}
                if outcome.no_majority
                else {"$set": {"open_for_voting": False, "decided": True}}
            )
            await self._packs.update_one(
                {"pack_id": row.pack_id}, close, session=session
            )
            return VoteRecorded(votes_cast=len(ballot), outcome=outcome)

        return await with_transaction(self._client, _do)

    async def unlock_for_changed_numbers(self, pack_id: str) -> bool:
        """One transaction, guarded on ``decided`` (and, for packs stored
        before that flag existed, on there being no decisive outcome row);
        writing the pack conflicts with a concurrent ``record_vote`` on it,
        so exactly one of "decide the round" and "unlock" wins.
        """

        async def _do(session: AsyncClientSession) -> bool:
            decisive = await self._outcomes.find_one(
                {"outcome.pack_id": pack_id, "outcome.no_majority": False},
                session=session,
            )
            if decisive is not None:
                return False
            pack = await self._packs.find_one_and_update(
                {"pack_id": pack_id, "decided": {"$ne": True}},
                {"$set": {"open_for_voting": False}},
                session=session,
            )
            if pack is None:
                if await self._packs.find_one({"pack_id": pack_id}, session=session):
                    return False
                raise NotFoundError(f"no pack {pack_id}")
            await self._votes.collection.update_many(
                {"pack_id": pack_id, "round": pack.current_round},
                {"$set": {"voided": True}},
                session=session,
            )
            return True

        return await with_transaction(self._client, _do)

    async def votes_for_round(self, pack_id: str, round_: int) -> list[_VoteRow]:
        return await self._votes.find({"pack_id": pack_id, "round": round_})

    async def void_votes(self, pack_id: str, round_: int) -> None:
        await self._votes.collection.update_many(
            {"pack_id": pack_id, "round": round_}, {"$set": {"voided": True}}
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
