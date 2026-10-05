"""Two S5 race/ordering defects from the adversarial audit, each pinned by
a regression test that failed before the fix:

(a) ``POST /questions/{q}/answers`` with ``numbers_changed=true`` on a pack
    whose round already reached a decisive outcome used to void the votes
    that outcome was counted from and leave the outcome standing -- a
    decided case with no valid votes behind it. It must be refused (409,
    ``case_decided``) and change nothing.
(b) ``cast_vote`` inserted the vote and *then* counted the round, as two
    separate storage steps. Two members completing quorum concurrently
    could both insert (4 votes in a 3-seat round), both tally, and bump
    the round twice. Recording a vote, the quorum check and closing the
    round must be one atomic step (``DecisionStore.record_vote``) that
    exactly one caller wins.

(b) cannot be provoked through ``TestClient`` (it serialises requests), so
it drives the real app through ``httpx.ASGITransport`` with two requests in
flight at once, over an in-memory store that yields to the event loop
around every storage call -- the shape of a real Mongo round trip, which
is exactly where the old insert-then-count sequence interleaved.
"""

from __future__ import annotations

import asyncio
import inspect
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from fcrm_common.auth import current_user
from fcrm_common.authz import OPAClient, install_authz
from fcrm_common.errors import UnauthorizedError, install_error_handlers
from fcrm_contracts.common import Principal
from fcrm_contracts.enums import Role
from fcrm_decision.routes import router
from fcrm_decision.store import InMemoryDecisionStore

CHECKS = {
    "numbers_locked": True,
    "conditions_drafted": True,
    "all_review_items_decided": True,
    "boundary_and_band_acknowledged": True,
}


class _YieldingStore(InMemoryDecisionStore):
    """``InMemoryDecisionStore`` whose every public coroutine yields to the
    event loop before and after it runs, as a network round trip would.
    """

    def __getattribute__(self, name: str) -> Any:
        attr = super().__getattribute__(name)
        if name.startswith("_") or not inspect.iscoroutinefunction(attr):
            return attr

        async def _yielding(*args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(0)
            result = await attr(*args, **kwargs)
            await asyncio.sleep(0)
            return result

        return _yielding


def _header_principal(request: Request) -> Principal:
    """Per-request caller from test headers, so two concurrent requests can
    be two different members (a shared override box cannot).
    """
    sub = request.headers.get("x-test-sub")
    role = request.headers.get("x-test-role")
    if not sub or not role:
        raise UnauthorizedError("Bearer token required.")
    return Principal(sub=sub, roles=[Role(role)], name=sub, typ="access")


def _app(store: InMemoryDecisionStore, opa: OPAClient) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(router)
    app.state.store = store
    app.dependency_overrides[current_user] = _header_principal
    install_authz(app.state, opa)
    return app


def _as(role: Role, sub: str) -> dict[str, str]:
    return {"x-test-sub": sub, "x-test-role": role.value}


def _vote_body(pack_id: str, member: str, option: str, round_: int = 1) -> dict:
    return {
        "pack_id": pack_id,
        "member_id": member,
        "round": round_,
        "option": option,
        "rationale": f"{member} votes {option}.",
    }


def _build(client: Any, case_id: str) -> str:
    response = client.post(
        "/packs",
        json={"case_id": case_id, "snapshot": {"case_id": case_id, "checks": CHECKS}},
        headers=_as(Role.SERVICE, "svc:caseflow"),
    )
    assert response.status_code == 200, response.text
    pack_id: str = response.json()["pack_id"]
    return pack_id


# ── (a) an answer that "changes numbers" cannot unlock a decided pack ────


def test_numbers_changed_after_a_decisive_outcome_is_refused_and_changes_nothing(
    allow_all_opa: OPAClient,
) -> None:
    store = InMemoryDecisionStore()
    client = TestClient(_app(store, allow_all_opa))
    pack_id = _build(client, "FCRM-2026-9201")
    for member, option in (
        ("u-jokafor", "approve_with_conditions"),
        ("u-amendes", "approve_with_conditions"),
        ("u-lchen", "defer"),
    ):
        voted = client.post(
            f"/packs/{pack_id}/votes",
            json=_vote_body(pack_id, member, option),
            headers=_as(Role.COMMITTEE, member),
        )
        assert voted.status_code == 200, voted.text
    decided = client.get(
        f"/packs/{pack_id}/outcome", headers=_as(Role.ANALYST, "u-riyer")
    ).json()
    assert decided["outcome"] == "approve_with_conditions"

    question_id = client.post(
        f"/packs/{pack_id}/questions",
        json={"pack_id": pack_id, "asked_by": "u-lchen", "text": "Is 4.5 right?"},
        headers=_as(Role.COMMITTEE, "u-lchen"),
    ).json()["question_id"]
    unlock = client.post(
        f"/questions/{question_id}/answers",
        json={
            "pack_id": pack_id,
            "question_id": question_id,
            "answered_by": "u-riyer",
            "text": "CTL-T01 corrected after review.",
            "numbers_changed": True,
        },
        headers=_as(Role.ANALYST, "u-riyer"),
    )
    assert unlock.status_code == 409, unlock.text
    assert unlock.json()["type"].endswith("/conflict")

    # Nothing moved: the counted votes are still valid and the outcome stands.
    round_votes = asyncio.run(store.votes_for_round(pack_id, 1))
    assert len(round_votes) == 3
    assert not any(v.voided for v in round_votes)
    after = client.get(
        f"/packs/{pack_id}/outcome", headers=_as(Role.ANALYST, "u-riyer")
    ).json()
    assert after == decided


# ── (b) completing quorum is atomic: one winner, never a fourth vote ─────


async def test_two_members_completing_quorum_at_once_store_three_votes_and_tally_once(
    allow_all_opa: OPAClient,
) -> None:
    store = _YieldingStore()
    transport = httpx.ASGITransport(app=_app(store, allow_all_opa))
    async with httpx.AsyncClient(transport=transport, base_url="http://s5") as client:
        response = await client.post(
            "/packs",
            json={
                "case_id": "FCRM-2026-9202",
                "snapshot": {"case_id": "FCRM-2026-9202", "checks": CHECKS},
            },
            headers=_as(Role.SERVICE, "svc:caseflow"),
        )
        pack_id = response.json()["pack_id"]
        for member, option in (("u-jokafor", "approve"), ("u-amendes", "defer")):
            voted = await client.post(
                f"/packs/{pack_id}/votes",
                json=_vote_body(pack_id, member, option),
                headers=_as(Role.COMMITTEE, member),
            )
            assert voted.status_code == 200, voted.text

        # Two different members race for the third seat. Both vote
        # "reject", so whichever wins, round 1 is a 1-1-1 split.
        racers = ("u-lchen", "u-fourth")
        results = await asyncio.gather(
            *(
                client.post(
                    f"/packs/{pack_id}/votes",
                    json=_vote_body(pack_id, member, "reject"),
                    headers=_as(Role.COMMITTEE, member),
                )
                for member in racers
            )
        )

    statuses = sorted(r.status_code for r in results)
    assert statuses == [200, 409], [r.text for r in results]
    winner = next(r for r in results if r.status_code == 200).json()
    assert winner["round"] == 1
    assert winner["no_majority"] is True
    assert winner["outcome"] is None

    assert len(await store.votes_for_round(pack_id, 1)) == 3
    pack = await store.get_pack(pack_id)
    assert pack is not None
    assert pack.current_round == 2  # bumped exactly once
    assert await store.votes_for_round(pack_id, 2) == []
    assert (await store.get_outcome(pack_id, 1)) is not None
    assert (await store.get_outcome(pack_id, 2)) is None


@pytest.mark.parametrize("racers", [2, 3])
async def test_many_concurrent_votes_on_an_empty_round_never_exceed_quorum(
    allow_all_opa: OPAClient, racers: int
) -> None:
    """``racers + 2`` members all vote at once on a fresh round: exactly
    three land, exactly one of them receives the outcome.
    """
    store = _YieldingStore()
    transport = httpx.ASGITransport(app=_app(store, allow_all_opa))
    async with httpx.AsyncClient(transport=transport, base_url="http://s5") as client:
        pack_id = (
            await client.post(
                "/packs",
                json={
                    "case_id": "FCRM-2026-9203",
                    "snapshot": {"case_id": "FCRM-2026-9203", "checks": CHECKS},
                },
                headers=_as(Role.SERVICE, "svc:caseflow"),
            )
        ).json()["pack_id"]
        members = [f"u-member-{i}" for i in range(racers + 2)]
        results = await asyncio.gather(
            *(
                client.post(
                    f"/packs/{pack_id}/votes",
                    json=_vote_body(pack_id, m, "approve"),
                    headers=_as(Role.COMMITTEE, m),
                )
                for m in members
            )
        )

    accepted = [r for r in results if r.status_code == 200]
    assert len(accepted) == 3
    assert sum(1 for r in accepted if "outcome" in r.json()) == 1
    assert all(r.status_code == 409 for r in results if r.status_code != 200)
    assert len(await store.votes_for_round(pack_id, 1)) == 3
