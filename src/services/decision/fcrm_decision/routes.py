"""S5's HTTP surface: frozen pack snapshots, hidden votes, the 2-of-3
tally, release checks and the R6 committee-question path.

Three product rules are enforced here, not just in the pure modules this
layer composes:

- **R2 (hidden votes):** no route, including the one a voter just posted
  to, ever returns another member's vote (or even this member's own, read
  back) before the round reaches quorum (``QUORUM = 3``). Before quorum,
  every read answers :class:`VotesHidden` -- a count, never content.
- **ADR-08 (never auto-resolve):** the tally that runs automatically when
  the third vote lands can only ever produce one of the four
  :class:`fcrm_contracts.enums.Outcome` values *or* ``no_majority``; a
  split is reported and a new round opens on the same pack, never
  defaulted to any outcome including ``defer``.
- **R6 (committee question -> numbers changed -> pack v2):** answering a
  question with ``numbers_changed=True`` closes the current pack to new
  votes immediately and voids its already-cast votes (kept, not deleted);
  the next ``POST /packs`` call for the same case -- made by whatever
  caller (H1) detected the number actually changed -- naturally produces
  a new pack version because the content hash no longer matches, which is
  exactly the re-request: every member faces a fresh, empty vote round on
  the new pack id.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Union

from fastapi import APIRouter, Depends, Query, Request
from fcrm_common.errors import ConflictError, NotFoundError, RuleViolationError
from fcrm_common.ids import new_id
from fcrm_common.time import utcnow
from fcrm_contracts.api.decision import (
    CommitteeAnswer,
    CommitteeQuestion,
    OutcomeSet,
    Pack,
    PackBuild,
    ReleaseChecks,
    RevealedVote,
    VoteCast,
    VotesHidden,
    VotesRevealed,
)

from fcrm_decision.outcomes import tally_round
from fcrm_decision.packs import content_hash
from fcrm_decision.release_checks import evaluate_release_checks
from fcrm_decision.store import QUORUM, DecisionStore, _PackRow, _VoteRow

logger = logging.getLogger(__name__)

router = APIRouter(tags=["decision"])


def get_store(request: Request) -> DecisionStore:
    """The pack/vote store wired onto ``app.state`` at startup
    (``lifespan.py``) -- a FastAPI dependency so routes stay testable with
    ``app.state.store`` swapped for :class:`fcrm_decision.store.
    InMemoryDecisionStore`.
    """
    store: DecisionStore = request.app.state.store
    return store


def _checks_from_snapshot(snapshot: dict[str, Any]) -> ReleaseChecks:
    """The pack's frozen content carries its own release-check state
    (``snapshot["checks"]``, filled by the caller from S1's locked case
    view) -- missing or malformed fields default to ``False`` (fail
    closed: an incomplete snapshot never looks releasable).
    """
    raw = snapshot.get("checks")
    data = raw if isinstance(raw, dict) else {}

    def passed(name: str) -> bool:
        # Only a literal JSON true counts. bool("false") and bool(1) are
        # True, which would release a pack on a malformed flag (fail open).
        return data.get(name) is True

    return evaluate_release_checks(
        numbers_locked=passed("numbers_locked"),
        conditions_drafted=passed("conditions_drafted"),
        all_review_items_decided=passed("all_review_items_decided"),
        boundary_and_band_acknowledged=passed("boundary_and_band_acknowledged"),
    )


@router.post("/release-checks", response_model=ReleaseChecks)
async def check_release(body: ReleaseChecks) -> ReleaseChecks:
    """Re-derive the four release checks through the pure
    ``evaluate_release_checks`` rather than trusting a pre-built
    ``ReleaseChecks`` object -- a thin, stateless wrapper a caller can poll
    before assembling a pack's snapshot.
    """
    return evaluate_release_checks(**body.model_dump())


@router.post("/packs", response_model=Pack)
async def build_or_reuse_pack(
    body: PackBuild, store: DecisionStore = Depends(get_store)
) -> Pack:
    """Build a new pack version, or hand back the current one unchanged.

    ``pack_unchanged`` (by content hash) is a no-op: the same pack id and
    version come back, no new row is written. Any real content change --
    including a number corrected after an R6 unlock -- mints a new pack id
    and version, and supersedes the previous one (its votes are voided,
    never deleted: design A §3.6 "pack v2").
    """
    checks = _checks_from_snapshot(body.snapshot)
    new_hash = content_hash(body.snapshot)
    latest = await store.latest_pack(body.case_id)
    if (
        latest is not None
        and latest.superseded_by is None
        and content_hash(latest.content) == new_hash
    ):
        return latest.to_pack(new_hash)

    if latest is not None:
        decided = await store.get_outcome(latest.pack_id)
        if decided is not None and not decided.no_majority:
            raise ConflictError(
                "this case already has a committee outcome; a new pack cannot "
                "be built for it",
                rule_id="case_decided",
            )

    version = latest.version + 1 if latest is not None else 1
    row = _PackRow(
        pack_id=new_id("pack"),
        case_id=body.case_id,
        version=version,
        content=body.snapshot,
        checks=checks,
        released_at=utcnow() if checks.all_passed else None,
        open_for_voting=checks.all_passed,
    )
    await store.insert_pack(row)
    if latest is not None:
        await store.mark_superseded(latest.pack_id, row.pack_id)
        await store.void_votes(latest.pack_id, latest.current_round)
    return row.to_pack(new_hash)


@router.get("/packs/{pack_id}", response_model=Pack)
async def get_pack(pack_id: str, store: DecisionStore = Depends(get_store)) -> Pack:
    """A pack by id, for a committee member's or analyst's own lookup."""
    row = await _require_pack(store, pack_id)
    return row.to_pack(content_hash(row.content))


async def _require_pack(store: DecisionStore, pack_id: str) -> _PackRow:
    """The stored pack row, or :class:`NotFoundError`."""
    row = await store.get_pack(pack_id)
    if row is None:
        raise NotFoundError(f"no pack {pack_id}")
    return row


@router.post("/packs/{pack_id}/votes")
async def cast_vote(
    pack_id: str,
    body: VoteCast,
    store: DecisionStore = Depends(get_store),
) -> Union[VotesHidden, OutcomeSet]:
    """Cast one committee member's vote.

    Hidden until quorum (R2): the response is always either
    ``VotesHidden`` (a count, never a vote) while fewer than
    :data:`QUORUM` members have voted this round, or the freshly computed
    ``OutcomeSet`` *only once the third vote has just landed* -- the
    voter who completes the round learns the outcome because quorum is,
    by definition, reached; nobody before that point can.
    """
    if body.pack_id != pack_id:
        raise ConflictError(
            "pack_id in the body must match the path", rule_id="pack_id_mismatch"
        )
    row = await _require_pack(store, pack_id)
    if not row.open_for_voting:
        raise RuleViolationError(
            "pack is not open for voting (release checks incomplete, "
            "superseded, or awaiting corrected numbers)",
            rule_id="pack_not_open",
        )
    if body.round != row.current_round:
        raise ConflictError(
            f"stale round: this pack's current round is {row.current_round}",
            rule_id="stale_round",
        )
    if not body.rationale.strip():
        raise RuleViolationError(
            "a rationale is required on every vote (R2)", rule_id="R2"
        )
    await store.insert_vote(
        _VoteRow(
            pack_id=pack_id,
            round=body.round,
            member_id=body.member_id,
            option=body.option,
            rationale=body.rationale,
            cast_at=utcnow(),
        )
    )
    cast_votes = await store.votes_for_round(pack_id, body.round)
    if len(cast_votes) < QUORUM:
        return VotesHidden(
            pack_id=pack_id, round=body.round, votes_cast=len(cast_votes), quorum=QUORUM
        )
    return await _tally_and_store(store, row, body.round, cast_votes)


async def _tally_and_store(
    store: DecisionStore,
    row: _PackRow,
    round_: int,
    cast_votes: list[_VoteRow],
) -> OutcomeSet:
    """Quorum just reached: tally the round once (ADR-08) and persist the
    result. ``no_majority`` opens the next round on the same pack and is
    never resolved to a default outcome; a majority closes the pack to
    further votes.
    """
    result = tally_round([v.option.value for v in cast_votes], round_)
    outcome = OutcomeSet(
        case_id=row.case_id,
        pack_id=row.pack_id,
        round=round_,
        outcome=result.outcome,
        no_majority=result.no_majority,
    )
    await store.save_outcome(outcome)
    if result.no_majority:
        await store.bump_round(row.pack_id)
        logger.info(
            "no majority; re-vote opened",
            extra={
                "pack_id": row.pack_id,
                "round": round_,
                "next_round": result.next_round,
            },
        )
    else:
        await store.set_open_for_voting(row.pack_id, False)
        logger.info(
            "committee outcome decided",
            extra={"pack_id": row.pack_id, "round": round_, "outcome": result.outcome},
        )
    return outcome


@router.get("/packs/{pack_id}/votes")
async def vote_status(
    pack_id: str,
    round_: Optional[int] = Query(default=None, alias="round"),
    store: DecisionStore = Depends(get_store),
) -> Union[VotesHidden, VotesRevealed]:
    """Before quorum: :class:`VotesHidden` (a count only, R2). Once the
    requested round has been tallied, its votes are revealed with full
    rationale and the tally -- never before.
    """
    row = await _require_pack(store, pack_id)
    target_round = round_ if round_ is not None else row.current_round
    outcome = await store.get_outcome(pack_id, target_round)
    cast_votes = await store.votes_for_round(pack_id, target_round)
    if outcome is None:
        return VotesHidden(
            pack_id=pack_id,
            round=target_round,
            votes_cast=len(cast_votes),
            quorum=QUORUM,
        )
    tally: dict[str, int] = {}
    for vote in cast_votes:
        tally[vote.option.value] = tally.get(vote.option.value, 0) + 1
    return VotesRevealed(
        pack_id=pack_id,
        round=target_round,
        votes=[
            RevealedVote(member_id=v.member_id, option=v.option, rationale=v.rationale)
            for v in cast_votes
        ],
        tally=tally,
    )


@router.get("/packs/{pack_id}/outcome", response_model=OutcomeSet)
async def get_outcome(
    pack_id: str, store: DecisionStore = Depends(get_store)
) -> OutcomeSet:
    """The persisted tally result for a pack's latest round.

    Raises :class:`ConflictError` (409) while quorum has not yet been
    reached -- there is no outcome to read, and none is ever invented.
    """
    await _require_pack(store, pack_id)
    outcome = await store.get_outcome(pack_id)
    if outcome is None:
        raise ConflictError(
            "not yet decided: quorum has not been reached", rule_id="no_quorum"
        )
    return outcome


@router.post("/packs/{pack_id}/questions")
async def ask_question(
    pack_id: str,
    body: CommitteeQuestion,
    store: DecisionStore = Depends(get_store),
) -> dict[str, Any]:
    """R6: open a committee question thread on ``pack_id``, visible to
    every member and the analyst. There is no ``Thread`` shape in
    ``fcrm_contracts.api.decision`` yet, so the response echoes the
    question with a minted ``question_id`` as a plain object rather than
    inventing a new contract type.
    """
    if body.pack_id != pack_id:
        raise ConflictError(
            "pack_id in the body must match the path", rule_id="pack_id_mismatch"
        )
    await _require_pack(store, pack_id)
    question_id = new_id("thr")
    await store.insert_question(question_id, body)
    return {"question_id": question_id, **body.model_dump()}


@router.post("/questions/{question_id}/answers")
async def answer_question(
    question_id: str,
    body: CommitteeAnswer,
    store: DecisionStore = Depends(get_store),
) -> dict[str, Any]:
    """R6: attach the analyst's answer as a pack addendum.

    ``numbers_changed=True`` unlocks the pack immediately: it stops
    accepting votes and its already-cast votes for the open round are
    voided (kept for audit, never deleted). No new pack is built here --
    the real "did a number change" detection and the corrected content
    both live with whatever calls this service (H1 caseflow); S5's job is
    only to act on the flag once it is told. The next ``POST /packs`` for
    this case, carrying the corrected snapshot, is what actually produces
    pack v2 and so re-requests every vote (a fresh, empty round on a new
    pack id).
    """
    if body.question_id != question_id:
        raise ConflictError(
            "question_id in the body must match the path",
            rule_id="question_id_mismatch",
        )
    question = await store.get_question(question_id)
    if question is None:
        raise NotFoundError(f"no committee question {question_id}")
    if body.numbers_changed and not body.text.strip():
        raise RuleViolationError(
            "unlocking a pack for changed numbers requires a reason (R6)",
            rule_id="R6",
        )
    addendum_id = new_id("addm")
    await store.insert_answer(addendum_id, body)
    if body.numbers_changed:
        pack_row = await _require_pack(store, question.pack_id)
        await store.set_open_for_voting(question.pack_id, False)
        await store.void_votes(question.pack_id, pack_row.current_round)
        logger.info(
            "committee answer changed a number; pack unlocked pending pack v2",
            extra={"pack_id": question.pack_id},
        )
    return {"addendum_id": addendum_id, **body.model_dump()}
