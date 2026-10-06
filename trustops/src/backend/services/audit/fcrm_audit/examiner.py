"""S4's examiner: a question is stored and announced, H1 has the examiner agent
answer it from the chain, and the answer is stored here and handed back.

**Why S4.** The examiner reads nothing but the audit chain, and S4 owns the
chain. A question is therefore asked of S4, which checks the case has a chain at
all, keeps the question, announces ``examiner.asked`` for H1 and waits for the
answer; H1 posts the answer back through a service-only route. Neither
collection is ever updated: the question is inserted once, the answer is
inserted once (``_id`` = the question's id), and whether a question is answered
is a join. Both live in the ``fcrm_audit`` database under the insert-only role
(``ops/mongo/init/02-users.js``).

**Waiting.** ``POST /examiner/ask`` waits up to ``examiner_wait_s`` for the
answer. An answer stored by this process wakes the waiter at once; otherwise
the store is polled every ``examiner_poll_s`` (another replica may have stored
it). A caller that is not answered in time gets 202 and the question id, and
reads the answer later.

**No outbox.** The audit role cannot update, so a relay that marks rows
published is impossible here. S4 publishes ``examiner.asked`` directly after the
question is stored, with a deadline; a failed publish is a 503 and the stored
question simply stays unanswered (nothing was decided by it).

**Citations.** An answer cites records by ``seq``. S4 refuses (422) an answer
that cites a record the chain does not hold, whatever H1 says: the check is two
lines against the chain head and it keeps a fabricated citation out of the
store even if H1 has a bug.
"""

from __future__ import annotations

import asyncio
import logging
import weakref
from dataclasses import dataclass
from datetime import datetime
from time import monotonic
from typing import Any, Final, Optional, Protocol

from fcrm_common.broker import BROKER_ERRORS
from fcrm_common.errors import (
    ConflictError,
    NotFoundError,
    UpstreamUnavailableError,
)
from fcrm_common.errors import ValidationError as FcrmValidationError
from fcrm_common.events import Publisher, build_envelope, default_exchange, publish
from fcrm_common.ids import new_id
from fcrm_contracts.api.audit_oversight import (
    ExaminerAnswerIn,
    ExaminerAnswerOut,
    ExaminerAskRequest,
    ExaminerPending,
    ExaminerSentenceOut,
)
from fcrm_contracts.events.payloads import ExaminerAsked
from pymongo.asynchronous.collection import AsyncCollection
from pymongo.errors import DuplicateKeyError, PyMongoError

from fcrm_audit.store import AuditStore, Stored, fingerprint, mongo_safe_now

logger = logging.getLogger(__name__)

EVENT_EXAMINER_ASKED: Final = "examiner.asked"
#: A deadline for the one publish; the broker confirms in milliseconds.
PUBLISH_TIMEOUT_S: Final = 5.0
#: What the answer points at besides the records: the rules it was read under.
MAX_REFERENCES: Final = 8


@dataclass(frozen=True)
class QuestionRow:
    """A stored question.

    Attributes:
        question_id: ``exq_`` plus a ULID.
        case_id: The case the question is about.
        question: What was asked.
        asked_by: The principal's id.
        asked_at: When S4 stored it.
    """

    question_id: str
    case_id: str
    question: str
    asked_by: str
    asked_at: datetime

    def document(self) -> dict[str, Any]:
        """The ``examiner_questions`` row."""
        return {
            "_id": self.question_id,
            "case_id": self.case_id,
            "question": self.question,
            "asked_by": self.asked_by,
            "asked_at": self.asked_at,
        }

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> QuestionRow:
        """The row back from its stored document."""
        return cls(
            question_id=str(doc["_id"]),
            case_id=str(doc["case_id"]),
            question=str(doc["question"]),
            asked_by=str(doc["asked_by"]),
            asked_at=doc["asked_at"],
        )


@dataclass(frozen=True)
class AnswerRow:
    """A stored answer, without the question text (the question row holds it)."""

    question_id: str
    case_id: str
    run_id: str
    refused: bool
    sentences: tuple[ExaminerSentenceOut, ...]
    references: tuple[str, ...]
    refusal_reason: Optional[str]
    request_fingerprint: str
    answered_at: datetime

    def document(self) -> dict[str, Any]:
        """The ``examiner_answers`` row (``_id`` is the question's id)."""
        return {
            "_id": self.question_id,
            "case_id": self.case_id,
            "run_id": self.run_id,
            "refused": self.refused,
            "sentences": [s.model_dump(mode="json") for s in self.sentences],
            "references": list(self.references),
            "refusal_reason": self.refusal_reason,
            "fingerprint": self.request_fingerprint,
            "answered_at": self.answered_at,
        }

    @classmethod
    def from_document(cls, doc: dict[str, Any]) -> AnswerRow:
        """The row back from its stored document."""
        return cls(
            question_id=str(doc["_id"]),
            case_id=str(doc["case_id"]),
            run_id=str(doc["run_id"]),
            refused=bool(doc["refused"]),
            sentences=tuple(
                ExaminerSentenceOut.model_validate(s) for s in doc["sentences"]
            ),
            references=tuple(str(r) for r in doc["references"]),
            refusal_reason=doc.get("refusal_reason"),
            request_fingerprint=str(doc["fingerprint"]),
            answered_at=doc["answered_at"],
        )

    def out(self, question: QuestionRow) -> ExaminerAnswerOut:
        """The answer as the web reads it."""
        return ExaminerAnswerOut(
            question_id=self.question_id,
            question=question.question,
            case_id=self.case_id,
            refused=self.refused,
            sentences=list(self.sentences),
            references=list(self.references),
            refusal_reason=self.refusal_reason,
        )


class ExaminerStore(Protocol):
    """What the examiner routes depend on; see the module docstring."""

    async def add_question(self, row: QuestionRow) -> None:
        """Insert a question."""
        ...

    async def get_question(self, question_id: str) -> Optional[QuestionRow]:
        """The question, or ``None``."""
        ...

    async def add_answer(self, row: AnswerRow) -> Stored[AnswerRow]:
        """Insert the answer to a question once. The same answer again is a
        replay (``replayed=True``, nothing written).

        Raises:
            fcrm_common.errors.ConflictError: A different answer is already
                stored for the question.
        """
        ...

    async def get_answer(self, question_id: str) -> Optional[AnswerRow]:
        """The stored answer, or ``None``."""
        ...


def _same_answer(stored: AnswerRow, row: AnswerRow) -> bool:
    return stored.request_fingerprint == row.request_fingerprint


def _different_answer(question_id: str) -> ConflictError:
    return ConflictError(
        "This question already has a different answer.", question_id=question_id
    )


class InMemoryExaminerStore:
    """The unit tests' store: two dicts, atomic under asyncio (no ``await``
    between a read and its write).
    """

    def __init__(self) -> None:
        self._questions: dict[str, QuestionRow] = {}
        self._answers: dict[str, AnswerRow] = {}

    async def add_question(self, row: QuestionRow) -> None:
        self._questions[row.question_id] = row

    async def get_question(self, question_id: str) -> Optional[QuestionRow]:
        return self._questions.get(question_id)

    async def add_answer(self, row: AnswerRow) -> Stored[AnswerRow]:
        stored = self._answers.get(row.question_id)
        if stored is None:
            self._answers[row.question_id] = row
            return Stored(row)
        if _same_answer(stored, row):
            return Stored(stored, replayed=True)
        raise _different_answer(row.question_id)

    async def get_answer(self, question_id: str) -> Optional[AnswerRow]:
        return self._answers.get(question_id)


class MongoExaminerStore:
    """The real store over ``examiner_questions`` and ``examiner_answers``.

    Both are insert-only (the audit role grants ``find`` and ``insert`` and
    nothing else); the unique ``_id`` of ``examiner_answers`` decides a race
    between two posts of the answer.

    Args:
        questions: The ``examiner_questions`` collection.
        answers: The ``examiner_answers`` collection.
    """

    def __init__(
        self,
        questions: AsyncCollection[Any],
        answers: AsyncCollection[Any],
    ) -> None:
        self._questions = questions
        self._answers = answers

    async def add_question(self, row: QuestionRow) -> None:
        await self._questions.insert_one(row.document())

    async def get_question(self, question_id: str) -> Optional[QuestionRow]:
        doc = await self._questions.find_one({"_id": question_id})
        return QuestionRow.from_document(doc) if doc is not None else None

    async def add_answer(self, row: AnswerRow) -> Stored[AnswerRow]:
        try:
            await self._answers.insert_one(row.document())
        except DuplicateKeyError:
            doc = await self._answers.find_one({"_id": row.question_id})
            if doc is None:  # pragma: no cover - a duplicate that vanished
                raise ConflictError(
                    "The answer could not be stored.", question_id=row.question_id
                ) from None
            stored = AnswerRow.from_document(doc)
            if _same_answer(stored, row):
                return Stored(stored, replayed=True)
            raise _different_answer(row.question_id) from None
        return Stored(row)

    async def get_answer(self, question_id: str) -> Optional[AnswerRow]:
        doc = await self._answers.find_one({"_id": question_id})
        return AnswerRow.from_document(doc) if doc is not None else None


class _Waiters:
    """One :class:`asyncio.Event` per question being waited on, so an answer
    stored by this process wakes its waiter at once. Entries disappear when the
    last waiter leaves.
    """

    def __init__(self) -> None:
        self._events: weakref.WeakValueDictionary[str, asyncio.Event] = (
            weakref.WeakValueDictionary()
        )

    def event_for(self, question_id: str) -> asyncio.Event:
        """The event for ``question_id`` (a live reference keeps it)."""
        event = self._events.get(question_id)
        if event is None:
            event = asyncio.Event()
            self._events[question_id] = event
        return event

    def wake(self, question_id: str) -> None:
        """Wake whoever waits for ``question_id``'s answer."""
        event = self._events.get(question_id)
        if event is not None:
            event.set()


class ExaminerService:
    """Ask, answer and read, over the stores S4 already has.

    Args:
        chain: The audit store, for the chain head.
        store: The examiner's question and answer store.
        publisher: The broker adapter, or ``None`` when S4 runs without one
            (then asking is refused with a 503, never silently dropped).
        wait_s: How long :meth:`ask` waits for the answer.
        poll_s: How often it re-reads the store while waiting.
    """

    def __init__(
        self,
        chain: AuditStore,
        store: ExaminerStore,
        publisher: Optional[Publisher],
        *,
        wait_s: float,
        poll_s: float,
    ) -> None:
        self._chain = chain
        self._store = store
        self._publisher = publisher
        self._wait_s = wait_s
        self._poll_s = poll_s
        self._waiters = _Waiters()

    async def ask(
        self, request: ExaminerAskRequest, asked_by: str
    ) -> ExaminerAnswerOut | ExaminerPending:
        """Store the question, announce it and wait for the answer.

        Returns:
            The answer if it arrived within the wait, else
            :class:`ExaminerPending` (the route answers 202).

        Raises:
            fcrm_common.errors.NotFoundError: The case has no audit chain.
            fcrm_common.errors.UpstreamUnavailableError: S4 has no broker, or
                the announcement could not be published.
        """
        if self._publisher is None:
            logger.error(
                "examiner cannot announce a question: no broker",
                extra={"case_id": request.case_id},
            )
            raise UpstreamUnavailableError(
                "The examiner is not available: the audit service has no broker.",
                dependency="rabbitmq",
            )
        head = await self._chain.head(request.case_id)
        if head.seq == 0:
            raise NotFoundError(f"No audit chain for case {request.case_id}.")
        row = QuestionRow(
            question_id=new_id("exq"),
            case_id=request.case_id,
            question=request.question,
            asked_by=asked_by,
            asked_at=mongo_safe_now(),
        )
        event = self._waiters.event_for(row.question_id)
        await self._store.add_question(row)
        await self._announce(self._publisher, row)
        logger.info(
            "examiner question asked",
            extra={"case_id": row.case_id, "question_id": row.question_id},
        )
        answer = await self._wait_for(row, event)
        if answer is not None:
            return answer
        return ExaminerPending(question_id=row.question_id, case_id=row.case_id)

    async def _announce(self, publisher: Publisher, row: QuestionRow) -> None:
        """Publish ``examiner.asked`` for ``row`` under a deadline.

        Raises:
            UpstreamUnavailableError: The broker did not confirm in time or
                refused (logged with the case and question ids).
        """
        envelope = build_envelope(
            EVENT_EXAMINER_ASKED,
            ExaminerAsked(
                case_id=row.case_id,
                question_id=row.question_id,
                question=row.question,
                asked_by=row.asked_by,
            ).model_dump(mode="json"),
            case_id=row.case_id,
        )
        try:
            async with asyncio.timeout(PUBLISH_TIMEOUT_S):
                await publish(
                    publisher,
                    default_exchange(EVENT_EXAMINER_ASKED),
                    EVENT_EXAMINER_ASKED,
                    envelope,
                )
        except BROKER_ERRORS as exc:
            logger.error(
                "examiner.asked could not be published",
                extra={
                    "case_id": row.case_id,
                    "question_id": row.question_id,
                    "error": {"type": type(exc).__name__},
                },
            )
            raise UpstreamUnavailableError(
                "The question was stored but could not be announced.",
                dependency="rabbitmq",
                question_id=row.question_id,
            ) from exc

    async def _wait_for(
        self, row: QuestionRow, event: asyncio.Event
    ) -> Optional[ExaminerAnswerOut]:
        """The answer to ``row`` if it is stored within the wait, else ``None``."""
        deadline = monotonic() + self._wait_s
        while True:
            stored = await self._store.get_answer(row.question_id)
            if stored is not None:
                return stored.out(row)
            remaining = deadline - monotonic()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(
                    event.wait(), timeout=min(self._poll_s, remaining)
                )
            except TimeoutError:
                continue
            event.clear()

    async def answer(self, body: ExaminerAnswerIn) -> Stored[ExaminerAnswerOut]:
        """Store H1's answer to a question (once; the same answer again is a
        replay).

        Raises:
            fcrm_common.errors.NotFoundError: No such question.
            fcrm_common.errors.ValidationError: The answer is for another case,
                or cites a record the case's chain does not hold.
            fcrm_common.errors.ConflictError: A different answer is stored.
        """
        question = await self._store.get_question(body.question_id)
        if question is None:
            raise NotFoundError(f"No question {body.question_id}.")
        if question.case_id != body.case_id:
            raise FcrmValidationError(
                "The answer names another case than the question.",
                errors=[{"loc": "body.case_id", "msg": "not the question's case"}],
            )
        await self._check_citations(body)
        row = AnswerRow(
            question_id=body.question_id,
            case_id=body.case_id,
            run_id=body.run_id,
            refused=body.refused,
            sentences=tuple(body.sentences),
            references=tuple(body.references),
            refusal_reason=body.refusal_reason,
            request_fingerprint=fingerprint(body),
            answered_at=mongo_safe_now(),
        )
        try:
            stored = await self._store.add_answer(row)
        except PyMongoError:
            logger.exception(
                "examiner answer could not be stored",
                extra={"case_id": body.case_id, "question_id": body.question_id},
            )
            raise
        self._waiters.wake(body.question_id)
        logger.info(
            "examiner question answered",
            extra={
                "case_id": body.case_id,
                "question_id": body.question_id,
                "refused": body.refused,
                "replayed": stored.replayed,
            },
        )
        return Stored(stored.value.out(question), replayed=stored.replayed)

    async def _check_citations(self, body: ExaminerAnswerIn) -> None:
        """Refuse an answer citing a record beyond the chain's head.

        Raises:
            ValidationError: A sentence cites a ``seq`` the chain does not hold.
        """
        cited = {ref for sentence in body.sentences for ref in sentence.refs}
        if not cited:
            return
        head = await self._chain.head(body.case_id)
        unknown = sorted(ref for ref in cited if ref > head.seq)
        if unknown:
            logger.warning(
                "examiner answer cites records the chain does not hold",
                extra={
                    "case_id": body.case_id,
                    "question_id": body.question_id,
                    "unknown": unknown,
                    "head_seq": head.seq,
                },
            )
            raise FcrmValidationError(
                "The answer cites a record that is not in the chain.",
                errors=[
                    {"loc": "body.sentences", "msg": f"no record {ref}"}
                    for ref in unknown
                ],
            )

    async def read(self, question_id: str) -> ExaminerAnswerOut | ExaminerPending:
        """The answer to a question, or :class:`ExaminerPending` if it has none
        yet.

        Raises:
            fcrm_common.errors.NotFoundError: No such question.
        """
        question = await self._store.get_question(question_id)
        if question is None:
            raise NotFoundError(f"No question {question_id}.")
        stored = await self._store.get_answer(question_id)
        if stored is None:
            return ExaminerPending(question_id=question_id, case_id=question.case_id)
        return stored.out(question)
