"""S5's votes against a real MongoDB 8 replica set, through S5's real lifespan
(indexes, seed, outbox relay) and its routes.

The in-memory store is atomic because a lock serialises its transactions; that
proves nothing about Mongo, where every call is a network round trip and
concurrency is settled by write conflicts and a unique index. These tests race
real concurrent callers against one pack and check what the unit tests pin: a
round never holds more than three votes, it is tallied exactly once, a refused
vote leaves **no vote row and no outbox row**, no outbox row carries a vote's
option before the round is revealed, and an unlock and the deciding vote never
both win.

    docker run -d --name i3d-mongo -p 28117:28117 mongo:8.0 \\
        mongod --replSet rs0 --port 28117 --bind_ip_all
    docker exec i3d-mongo mongosh --quiet --port 28117 --eval \\
        'rs.initiate({_id:"rs0",members:[{_id:0,host:"127.0.0.1:28117"}]}).ok'
    export FCRM_TEST_MONGO_URI='mongodb://127.0.0.1:28117/?replicaSet=rs0'
    uv run --project src/backend --all-packages \\
        pytest tests/integration/live_mongo -m integration
"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

import pytest
from fcrm_contracts.enums import Role

if TYPE_CHECKING:
    from conftest import LiveDecision

pytestmark = pytest.mark.integration

CASE = "FCRM-2026-0151"
ANALYST = "u-riyer"
SERVICE = "svc:caseflow"
OKAFOR, MENDES, CHEN = "u-jokafor", "u-amendes", "u-lchen"
RATIONALE = "A written position recorded for the audit trail."
REASON = "The corridor review lowers the CTL-T01 rating, so the numbers change."


async def _released(
    live: LiveDecision,
    release_snapshot: Callable[..., dict[str, Any]],
    case_id: str = CASE,
) -> str:
    built = await live.http.post(
        "/packs",
        json={"case_id": case_id, "snapshot": release_snapshot(case_id)},
        headers=live.headers(Role.SERVICE, SERVICE),
    )
    assert built.status_code == 201, built.text
    pack_id: str = built.json()["pack_id"]
    released = await live.http.post(
        f"/packs/{pack_id}/release", headers=live.headers(Role.ANALYST, ANALYST)
    )
    assert released.status_code == 200, released.text
    return pack_id


def _vote(live: LiveDecision, pack_id: str, member: str, option: str) -> Any:
    return live.http.post(
        f"/packs/{pack_id}/votes",
        json={"vote": option, "rationale": RATIONALE},
        headers=live.headers(Role.COMMITTEE, member),
    )


async def _outbox(live: LiveDecision, event_type: str) -> list[dict[str, Any]]:
    return await live.db["outbox"].find({"type": event_type}).sort("_id", 1).to_list()


# ── a vote and its event commit together ─────────────────────────────────


async def test_a_vote_and_its_outbox_row_commit_together_and_the_relay_publishes_them(
    live_decision: LiveDecision, release_snapshot: Callable[..., dict[str, Any]]
) -> None:
    pack_id = await _released(live_decision, release_snapshot)
    for member, option in (
        (OKAFOR, "approve_with_conditions"),
        (MENDES, "approve_with_conditions"),
        (CHEN, "defer"),
    ):
        assert (await _vote(live_decision, pack_id, member, option)).status_code == 201

    assert await live_decision.db["votes"].count_documents({"pack_id": pack_id}) == 3
    assert len(await _outbox(live_decision, "vote.cast")) == 3
    assert len(await _outbox(live_decision, "vote.tallied")) == 1

    async with asyncio.timeout(5):
        while len(live_decision.publisher.sent) < 5:
            await asyncio.sleep(0.02)
    assert [e.type for e in live_decision.publisher.sent] == [
        "pack.released",
        "vote.cast",
        "vote.cast",
        "vote.cast",
        "vote.tallied",
    ]
    published = await live_decision.db["outbox"].find({"published_at": None}).to_list()
    assert published == []


async def test_a_duplicate_vote_leaves_neither_a_vote_row_nor_an_outbox_row(
    live_decision: LiveDecision, release_snapshot: Callable[..., dict[str, Any]]
) -> None:
    pack_id = await _released(live_decision, release_snapshot)
    assert (await _vote(live_decision, pack_id, OKAFOR, "approve")).status_code == 201
    votes_before = await live_decision.db["votes"].count_documents({"pack_id": pack_id})
    events_before = await live_decision.db["outbox"].count_documents({})

    again = await _vote(live_decision, pack_id, OKAFOR, "reject")

    assert again.status_code == 409
    assert again.json()["type"].endswith("/already_voted")
    assert (
        await live_decision.db["votes"].count_documents({"pack_id": pack_id})
        == votes_before
    )
    assert await live_decision.db["outbox"].count_documents({}) == events_before
    # ...and the pack is exactly as the first vote left it.
    pack = await live_decision.db["packs"].find_one({"pack_id": pack_id})
    assert pack is not None
    assert pack["current_round"] == 1
    assert pack["decided"] is False


async def test_no_outbox_row_carries_a_votes_option_before_the_round_is_revealed(
    live_decision: LiveDecision, release_snapshot: Callable[..., dict[str, Any]]
) -> None:
    pack_id = await _released(live_decision, release_snapshot)
    await _vote(live_decision, pack_id, OKAFOR, "reject")
    await _vote(live_decision, pack_id, MENDES, "defer")

    rows = (
        await live_decision.db["outbox"]
        .find({"type": {"$ne": "pack.released"}})
        .to_list()
    )
    assert [r["type"] for r in rows] == ["vote.cast", "vote.cast"]
    for row in rows:
        body = bytes(row["body"]).decode("utf-8")
        assert RATIONALE not in body
        assert "reject" not in body
        assert "defer" not in body
        assert set(json.loads(body)["payload"]) == {
            "pack_id",
            "round",
            "member_id",
            "cast_count",
            "quorum",
        }

    await _vote(live_decision, pack_id, CHEN, "reject")  # the third vote reveals
    (tallied,) = await _outbox(live_decision, "vote.tallied")
    assert RATIONALE in bytes(tallied["body"]).decode("utf-8")


# ── races on a real replica set ──────────────────────────────────────────


@pytest.mark.parametrize("racers", [4, 6])
async def test_concurrent_votes_on_one_round_never_exceed_quorum_and_tally_once(
    live_decision: LiveDecision,
    release_snapshot: Callable[..., dict[str, Any]],
    racers: int,
) -> None:
    pack_id = await _released(
        live_decision, release_snapshot, f"FCRM-2026-92{racers:02d}"
    )
    options = ["approve", "defer", "reject"]
    results = await asyncio.gather(
        *(
            _vote(live_decision, pack_id, f"u-m{i}", options[i % len(options)])
            for i in range(racers)
        )
    )

    # Every racer lands somewhere: a round fills with three; a split opens the
    # next round, and later racers (the vote carries no round) land in it.
    assert all(r.status_code in (201, 409) for r in results), [r.text for r in results]
    rows = await live_decision.db["votes"].find({"pack_id": pack_id}).to_list()
    per_round = Counter(row["round"] for row in rows)
    assert max(per_round.values()) <= 3  # never a fourth vote in a round
    full_rounds = [number for number, count in per_round.items() if count == 3]
    concluded = len(await _outbox(live_decision, "vote.tallied")) + len(
        await _outbox(live_decision, "vote.no_majority")
    )
    assert concluded == len(full_rounds)  # each full round concluded exactly once
    assert await live_decision.db["outcomes"].count_documents({}) - 65 == concluded
    assert len(await _outbox(live_decision, "vote.cast")) == len(rows)
    pack = await live_decision.db["packs"].find_one({"pack_id": pack_id})
    assert pack is not None
    if pack["decided"]:
        assert max(per_round) == pack["current_round"]  # nothing after a decision
    else:
        assert pack["current_round"] == len(full_rounds) + 1


async def test_unlock_and_deciding_vote_racing_never_both_win(
    live_decision: LiveDecision, release_snapshot: Callable[..., dict[str, Any]]
) -> None:
    pack_id = await _released(live_decision, release_snapshot)
    asked = await live_decision.http.post(
        f"/packs/{pack_id}/threads",
        json={"text": "Does the corridor review change the rating?"},
        headers=live_decision.headers(Role.COMMITTEE, OKAFOR),
    )
    thread_id = asked.json()["id"]
    for member in (OKAFOR, MENDES):
        assert (
            await _vote(live_decision, pack_id, member, "approve")
        ).status_code == 201

    vote, unlock = await asyncio.gather(
        _vote(live_decision, pack_id, CHEN, "defer"),
        live_decision.http.post(
            f"/threads/{thread_id}/answer",
            json={
                "text": "Yes, the corridor review lowers CTL-T01.",
                "numbers_changed": True,
                "unlock_reason": REASON,
            },
            headers=live_decision.headers(Role.ANALYST, ANALYST),
        ),
    )

    pack = await live_decision.db["packs"].find_one({"pack_id": pack_id})
    assert pack is not None
    if vote.status_code == 201:
        assert unlock.status_code == 409
        assert pack["decided"] is True
        assert pack.get("unlocked_at") is None
    else:
        assert unlock.status_code == 200
        assert vote.status_code == 409
        assert pack["decided"] is False
        assert pack["open_for_voting"] is False


# ── votes are voided, never deleted ──────────────────────────────────────


async def test_supersede_voids_the_votes_in_place_and_keeps_every_row(
    live_decision: LiveDecision, release_snapshot: Callable[..., dict[str, Any]]
) -> None:
    pack_id = await _released(live_decision, release_snapshot)
    await _vote(live_decision, pack_id, OKAFOR, "approve")
    await _vote(live_decision, pack_id, MENDES, "defer")

    response = await live_decision.http.post(
        f"/packs/{pack_id}/supersede",
        json={"reason": REASON},
        headers=live_decision.headers(Role.SERVICE, SERVICE),
    )

    assert response.status_code == 200
    rows = await live_decision.db["votes"].find({"pack_id": pack_id}).to_list()
    assert len(rows) == 2  # kept, not deleted
    assert all(row["voided"] is True for row in rows)
    shown = await live_decision.http.get(
        f"/packs/{pack_id}/votes", headers=live_decision.headers(Role.ANALYST, ANALYST)
    )
    assert shown.json()["cast_count"] == 0
    assert [e["type"] for e in await _outbox(live_decision, "pack.superseded")] == [
        "pack.superseded"
    ]
    (rerequested,) = await _outbox(live_decision, "vote.rerequested")
    payload = json.loads(bytes(rerequested["body"]))["payload"]
    assert sorted(payload["member_ids"]) == [MENDES, OKAFOR]


# ── ADR-31 on the real thing ─────────────────────────────────────────────


async def test_a_three_way_split_opens_round_two_and_a_system_thread_atomically(
    live_decision: LiveDecision, release_snapshot: Callable[..., dict[str, Any]]
) -> None:
    pack_id = await _released(live_decision, release_snapshot)
    for member, option in ((OKAFOR, "approve"), (MENDES, "defer"), (CHEN, "reject")):
        assert (await _vote(live_decision, pack_id, member, option)).status_code == 201

    pack = await live_decision.db["packs"].find_one({"pack_id": pack_id})
    assert pack is not None
    assert (pack["current_round"], pack["decided"], pack["open_for_voting"]) == (
        2,
        False,
        True,
    )
    outcome = await live_decision.db["outcomes"].find_one({"_id": f"{pack_id}:1"})
    assert outcome is not None
    assert outcome["outcome"]["no_majority"] is True
    assert outcome["outcome"]["outcome"] is None
    types = [
        r["type"]
        for r in await live_decision.db["outbox"].find({}).sort("_id", 1).to_list()
    ]
    assert types[-2:] == ["vote.no_majority", "vote.rerequested"]
    threads = await live_decision.db["questions"].find({"pack_id": pack_id}).to_list()
    assert [t["system"] for t in threads] == [True]
    # Round one is kept and readable; round two is empty and hidden.
    one = await live_decision.http.get(
        f"/packs/{pack_id}/votes",
        params={"round": 1},
        headers=live_decision.headers(Role.ANALYST, ANALYST),
    )
    assert one.json()["outcome"] == "no_majority"
    two = await live_decision.http.get(
        f"/packs/{pack_id}/votes", headers=live_decision.headers(Role.ANALYST, ANALYST)
    )
    assert two.json()["round"] == 2
    assert two.json()["cast_count"] == 0
