"""The committee's surface (ADR-30: the web's routes): the queue, a case's
packs, the votes (hidden until all three are in), the question threads and the
case outcome.

Three product rules are enforced here and in the modules this layer composes:

* **R2 (hidden votes).** No response carries a peer's choice before the round is
  tallied, and the third vote reveals and tallies together
  (:func:`fcrm_decision.views.votes_view` is the only place a stored vote becomes
  a readable choice). The caller always sees their own vote.
* **ADR-08 / ADR-31 (never auto-resolve).** A round ends in one of the four
  outcomes or ``no_majority``; a 1-1-1 split opens round two, it is never
  mapped to ``defer``.
* **R6 (a question opens a thread for everyone).** A member's question is
  visible to every member and the analyst; the answer is a pack addendum.

Identity comes from the token (D-23): the voter, the asker are the caller.
"""

from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Query
from fcrm_common.auth import require_roles
from fcrm_common.errors import NotFoundError
from fcrm_contracts.api.decision import (
    CaseOutcome,
    CommitteeQueueItem,
    CommitteeThread,
    OutcomeSet,
    PackSummary,
    PackView,
    ThreadAsk,
    VoteIn,
    VoteReceipt,
    VotesView,
)
from fcrm_contracts.common import Page, Principal

from fcrm_decision.commands_thread import ask_thread
from fcrm_decision.commands_vote import cast_vote
from fcrm_decision.deps import (
    COMMITTEE_ROLES,
    DEFAULT_LIMIT,
    MAX_LIMIT,
    READ_ROLES,
    DirectoryDep,
    StoreDep,
    committee_view,
    viewer_id,
    visible_pack,
)
from fcrm_decision.errors import NotTalliedError
from fcrm_decision.views import (
    case_outcome_view,
    pack_summary,
    pack_view,
    queue_item,
    thread_view,
    votes_view,
)

router = APIRouter()

Reader = Annotated[Principal, Depends(require_roles(*READ_ROLES))]
Member = Annotated[Principal, Depends(require_roles(*COMMITTEE_ROLES))]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


@router.get("/committee/queue", response_model=Page[CommitteeQueueItem])
async def committee_queue(
    principal: Member, store: StoreDep, limit: Limit = DEFAULT_LIMIT
) -> Page[CommitteeQueueItem]:
    """Released packs that are still the case's current one, newest first, each
    with the caller's own vote status and how many have voted (never how). One
    aggregation: no query per pack.
    """
    entries = await store.committee_queue(limit=limit)
    return Page(items=[queue_item(entry, principal.sub) for entry in entries])


@router.get("/cases/{case_id}/packs/latest", response_model=PackView)
async def latest_pack(case_id: str, principal: Reader, store: StoreDep) -> PackView:
    """The case's newest pack. A committee member gets the newest *released* one;
    a pack not yet released is the analyst's alone.
    """
    pack = await store.latest_pack(case_id, released_only=committee_view(principal))
    if pack is None:
        raise NotFoundError(f"no pack has been built for case {case_id}")
    return pack_view(pack, await store.addenda(pack.pack_id))


@router.get("/cases/{case_id}/packs", response_model=Page[PackSummary])
async def case_packs(
    case_id: str, principal: Reader, store: StoreDep, limit: Limit = DEFAULT_LIMIT
) -> Page[PackSummary]:
    """Every version of the case's pack, newest first; a superseded version stays
    readable (both packs are kept).
    """
    packs = await store.list_packs(
        case_id, released_only=committee_view(principal), limit=limit
    )
    return Page(items=[pack_summary(pack) for pack in packs])


@router.get("/packs/{pack_id}", response_model=PackView)
async def get_pack(pack_id: str, principal: Reader, store: StoreDep) -> PackView:
    """A pack by id, with its addenda."""
    pack = await visible_pack(store, principal, pack_id)
    return pack_view(pack, await store.addenda(pack_id))


@router.get("/packs/{pack_id}/votes", response_model=VotesView)
async def votes(
    pack_id: str,
    principal: Reader,
    store: StoreDep,
    directory: DirectoryDep,
    round_: Annotated[Optional[int], Query(alias="round", ge=1)] = None,
) -> VotesView:
    """One round's votes (the current one unless ``round`` is given).

    Until the round is tallied ``revealed`` is false and every member's choice
    but the caller's own is null: the choices are not sent, not merely hidden.
    A tallied round (including one that ended with no majority) shows every
    vote and its rationale together with the tally.
    """
    pack = await visible_pack(store, principal, pack_id)
    target = round_ if round_ is not None else pack.current_round
    if target > pack.current_round:
        raise NotFoundError(f"pack {pack_id} has no round {target}")
    ballot = await store.round_votes(pack_id, target)
    result = await store.get_outcome(pack_id, target)
    roster = await directory.members()
    return votes_view(
        pack_id=pack_id,
        round_=target,
        votes=ballot,
        outcome=result,
        roster=roster,
        viewer_id=viewer_id(principal),
    )


@router.post("/packs/{pack_id}/votes", status_code=201, response_model=VoteReceipt)
async def cast(
    pack_id: str, body: VoteIn, principal: Member, store: StoreDep
) -> VoteReceipt:
    """Cast the caller's vote in the pack's current round.

    The member is the verified caller, so no token can vote in another member's
    name and one-vote-per-member binds to real identity; a rationale is
    required on every vote (R2). The receipt says that the vote counted, never
    what it was: the choice is hidden until all three are in, and the member who
    casts the third finds the revealed round on their next read.
    """
    recorded = await cast_vote(
        store,
        pack_id=pack_id,
        member_id=principal.sub,
        option=body.vote,
        rationale=body.rationale,
    )
    return VoteReceipt(
        vote_id=recorded.vote_id, round=recorded.round, cast_n_of_3=recorded.cast_count
    )


@router.get("/packs/{pack_id}/outcome", response_model=OutcomeSet)
async def pack_outcome(pack_id: str, principal: Reader, store: StoreDep) -> OutcomeSet:
    """The pack's latest round result.

    Raises:
        NotTalliedError: No round has been tallied yet (409): there is no
            outcome to read, and none is ever invented.
    """
    await visible_pack(store, principal, pack_id)
    row = await store.get_outcome(pack_id)
    if row is None:
        raise NotTalliedError("Not yet decided: no round has been tallied.")
    return row.outcome


@router.get("/packs/{pack_id}/threads", response_model=Page[CommitteeThread])
async def threads(
    pack_id: str, principal: Reader, store: StoreDep, limit: Limit = DEFAULT_LIMIT
) -> Page[CommitteeThread]:
    """The pack's question threads, oldest first: visible to every member and the
    analyst (R6).
    """
    await visible_pack(store, principal, pack_id)
    rows = await store.list_threads(pack_id, limit=limit)
    return Page(items=[thread_view(row) for row in rows])


@router.post(
    "/packs/{pack_id}/threads", status_code=201, response_model=CommitteeThread
)
async def ask(
    pack_id: str, body: ThreadAsk, principal: Member, store: StoreDep
) -> CommitteeThread:
    """Open a question thread on a released pack, in the caller's own name."""
    thread = await ask_thread(
        store,
        pack_id=pack_id,
        asker_id=principal.sub,
        asker_name=principal.name,
        text=body.text,
    )
    return thread_view(thread)


@router.get("/cases/{case_id}/outcome", response_model=CaseOutcome)
async def case_outcome(
    case_id: str, _principal: Reader, store: StoreDep
) -> CaseOutcome:
    """What was decided for the case, and how: the committee's tally or the
    analyst's delegated close.

    Raises:
        NotFoundError: The case has no decisive outcome yet (a round that ended
            with no majority decided nothing).
    """
    row = await store.case_outcome(case_id)
    if row is None:
        raise NotFoundError(f"case {case_id} has no outcome")
    return case_outcome_view(row)
