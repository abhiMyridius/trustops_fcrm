"""S5's ordering and concurrency defects, each pinned by a regression test:

(a) An answer with ``numbers_changed`` on a pack whose round already reached a
    decisive outcome used to void the votes that outcome was counted from and
    leave the outcome standing: a decided case with no valid votes behind it.
    It must be refused (409 ``pack_decided``) and change nothing.
(b) Recording a vote, the quorum check and closing the round must be one atomic
    step that exactly one caller wins: two members completing quorum at once
    must not both insert (four votes in a three-seat round), both tally, or open
    round two twice.
(c) An unlock and the vote that decides the round cannot both win.

(b) and (c) cannot be provoked through ``TestClient`` (it serialises requests),
so they drive the real app through ``httpx.ASGITransport`` with the requests in
flight together, over an in-memory store that yields to the event loop around
every storage call: the shape of a real Mongo round trip. The store's
transaction is serialised by a lock the way Mongo serialises transactions on
one pack's document; ``tests/integration/live_mongo`` proves the real thing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fcrm_common.auth import current_user
from fcrm_common.authz import OPAClient, install_authz
from fcrm_common.errors import UnauthorizedError
from fcrm_contracts.api.identity import RosterUser
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from fcrm_decision.directory import StaticCommitteeDirectory
from fcrm_decision.main import create_app
from fcrm_decision.store import DecisionTx
from fcrm_decision.store_memory import InMemoryDecisionStore

OKAFOR, MENDES, CHEN = "u-jokafor", "u-amendes", "u-lchen"
ANALYST = "u-riyer"
SERVICE = "svc:caseflow"
RATIONALE = "A written position recorded for the audit trail."


class _YieldingTx:
    """A transaction whose every operation yields to the event loop before and
    after it runs, as a network round trip would.
    """

    def __init__(self, tx: DecisionTx) -> None:
        self._tx = tx

    def __getattr__(self, name: str) -> Any:
        operation = getattr(self._tx, name)

        async def _yielding(*args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(0)
            result = await operation(*args, **kwargs)
            await asyncio.sleep(0)
            return result

        return _yielding


class _YieldingStore(InMemoryDecisionStore):
    async def run[R](self, work: Callable[[DecisionTx], Awaitable[R]]) -> R:
        async def wrapped(tx: DecisionTx) -> R:
            return await work(_YieldingTx(tx))

        return await super().run(wrapped)


def _header_principal(request: Request) -> Principal:
    """Per-request caller from test headers, so concurrent requests can be
    different members (a shared override box cannot).
    """
    sub = request.headers.get("x-test-sub")
    role = request.headers.get("x-test-role")
    if not sub or not role:
        raise UnauthorizedError("Bearer token required.")
    return Principal(sub=sub, roles=[Role(role)], name=sub, typ="access")


def _as(role: Role, sub: str) -> dict[str, str]:
    return {"x-test-sub": sub, "x-test-role": role.value}


@pytest.fixture
def make_client(
    allow_all_opa: OPAClient, committee_roster: list[RosterUser]
) -> Callable[[InMemoryDecisionStore], httpx.AsyncClient]:
    def _make(store: InMemoryDecisionStore) -> httpx.AsyncClient:
        app: FastAPI = create_app()
        app.state.store = store
        app.state.directory = StaticCommitteeDirectory(committee_roster)
        app.dependency_overrides[current_user] = _header_principal
        install_authz(app.state, allow_all_opa)
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://s5"
        )

    return _make


async def _released(
    client: httpx.AsyncClient, snapshot: dict[str, Any], case_id: str
) -> str:
    built = await client.post(
        "/packs",
        json={"case_id": case_id, "snapshot": snapshot},
        headers=_as(Role.SERVICE, SERVICE),
    )
    assert built.status_code == 201, built.text
    pack_id: str = built.json()["pack_id"]
    released = await client.post(
        f"/packs/{pack_id}/release", headers=_as(Role.ANALYST, ANALYST)
    )
    assert released.status_code == 200, released.text
    return pack_id


def _vote(client: httpx.AsyncClient, pack_id: str, member: str, option: str) -> Any:
    return client.post(
        f"/packs/{pack_id}/votes",
        json={"vote": option, "rationale": RATIONALE},
        headers=_as(Role.COMMITTEE, member),
    )


# ── (a) an answer that "changes numbers" cannot unlock a decided pack ────


async def test_numbers_changed_after_a_decisive_outcome_is_refused_and_changes_nothing(
    make_client: Callable[[InMemoryDecisionStore], httpx.AsyncClient],
    release_snapshot: Callable[..., dict[str, Any]],
) -> None:
    store = InMemoryDecisionStore()
    async with make_client(store) as client:
        case_id = "FCRM-2026-9201"
        pack_id = await _released(client, release_snapshot(case_id), case_id)
        for member, option in (
            (OKAFOR, "approve_with_conditions"),
            (MENDES, "approve_with_conditions"),
            (CHEN, "defer"),
        ):
            assert (await _vote(client, pack_id, member, option)).status_code == 201
        thread = await client.post(
            f"/packs/{pack_id}/threads",
            json={"text": "Is 4.5 right?"},
            headers=_as(Role.COMMITTEE, CHEN),
        )
        # Closed means closed: a decided pack takes no more questions either.
        assert thread.status_code == 409
        decided = (
            await client.get(
                f"/packs/{pack_id}/outcome", headers=_as(Role.ANALYST, ANALYST)
            )
        ).json()
        assert decided["outcome"] == "approve_with_conditions"

    ballot = await store.round_votes(pack_id, 1)
    assert len(ballot) == 3
    assert not any(v.voided for v in ballot)
    row = await store.get_outcome(pack_id, 1)
    assert row is not None
    assert row.outcome.outcome is not None


# ── (b) completing quorum is atomic: one winner, never a fourth vote ─────


async def test_two_members_completing_quorum_at_once_store_three_votes_and_tally_once(
    make_client: Callable[[InMemoryDecisionStore], httpx.AsyncClient],
    release_snapshot: Callable[..., dict[str, Any]],
) -> None:
    store = _YieldingStore()
    async with make_client(store) as client:
        case_id = "FCRM-2026-9202"
        pack_id = await _released(client, release_snapshot(case_id), case_id)
        for member, option in ((OKAFOR, "approve"), (MENDES, "defer")):
            assert (await _vote(client, pack_id, member, option)).status_code == 201

        # Two different members race for the third seat. Both vote "reject",
        # so whichever wins, round 1 is a 1-1-1 split.
        results = await asyncio.gather(
            _vote(client, pack_id, CHEN, "reject"),
            _vote(client, pack_id, "u-fourth", "reject"),
        )

    # The split opens round two in the same transaction that tallies it, and
    # the vote carries no round, so the second racer's vote is the first vote
    # of round two: never a fourth vote in round one.
    assert [r.status_code for r in results] == [201, 201], [r.text for r in results]
    assert sorted(r.json()["round"] for r in results) == [1, 2]
    assert len(await store.round_votes(pack_id, 1)) == 3
    assert len(await store.round_votes(pack_id, 2)) == 1
    pack = await store.get_pack(pack_id)
    assert pack is not None
    assert pack.current_round == 2  # bumped exactly once
    assert (await store.get_outcome(pack_id, 1)) is not None
    assert (await store.get_outcome(pack_id, 2)) is None
    types = [e.type for e in store.events]
    assert types.count("vote.no_majority") == 1
    assert types.count("vote.rerequested") == 1
    assert types.count("vote.tallied") == 0


@pytest.mark.parametrize("racers", [2, 3])
async def test_many_concurrent_votes_on_an_empty_round_never_exceed_quorum(
    make_client: Callable[[InMemoryDecisionStore], httpx.AsyncClient],
    release_snapshot: Callable[..., dict[str, Any]],
    racers: int,
) -> None:
    """``racers + 2`` members all vote at once on a fresh round: exactly three
    land, the round is tallied once, and the refused votes leave no event.
    """
    store = _YieldingStore()
    async with make_client(store) as client:
        case_id = "FCRM-2026-9203"
        pack_id = await _released(client, release_snapshot(case_id), case_id)
        members = [f"u-member-{i}" for i in range(racers + 2)]
        results = await asyncio.gather(
            *(_vote(client, pack_id, member, "approve") for member in members)
        )

    accepted = [r for r in results if r.status_code == 201]
    assert len(accepted) == 3
    assert all(r.status_code == 409 for r in results if r.status_code != 201)
    assert len(await store.round_votes(pack_id, 1)) == 3
    types = [e.type for e in store.events]
    assert types.count("vote.cast") == 3
    assert types.count("vote.tallied") == 1


# ── (c) an unlock and the deciding vote cannot both win ──────────────────


async def test_an_unlock_racing_the_deciding_vote_leaves_one_consistent_result(
    make_client: Callable[[InMemoryDecisionStore], httpx.AsyncClient],
    release_snapshot: Callable[..., dict[str, Any]],
) -> None:
    store = _YieldingStore()
    async with make_client(store) as client:
        case_id = "FCRM-2026-9204"
        pack_id = await _released(client, release_snapshot(case_id), case_id)
        thread = await client.post(
            f"/packs/{pack_id}/threads",
            json={"text": "Does the corridor review change the rating?"},
            headers=_as(Role.COMMITTEE, OKAFOR),
        )
        thread_id = thread.json()["id"]
        for member, option in ((OKAFOR, "approve"), (MENDES, "approve")):
            assert (await _vote(client, pack_id, member, option)).status_code == 201

        vote, unlock = await asyncio.gather(
            _vote(client, pack_id, CHEN, "defer"),
            client.post(
                f"/threads/{thread_id}/answer",
                json={
                    "text": "Yes, the corridor review lowers CTL-T01.",
                    "numbers_changed": True,
                    "unlock_reason": "The corridor review lowers the CTL-T01 rating.",
                },
                headers=_as(Role.ANALYST, ANALYST),
            ),
        )

    # Exactly one of them won. Never: a decided pack whose numbers were unlocked.
    pack = await store.get_pack(pack_id)
    assert pack is not None
    if vote.status_code == 201:
        assert unlock.status_code == 409
        assert pack.decided is True
        assert pack.unlocked_at is None
    else:
        assert unlock.status_code == 200
        assert vote.status_code == 409
        assert pack.decided is False
        assert pack.open_for_voting is False
        assert pack.unlocked_at is not None
